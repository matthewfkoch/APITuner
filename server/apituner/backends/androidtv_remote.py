"""Android TV Remote protocol v2 backend (ADB-free).

Uses the `androidtvremote2` library (the same protocol the Google TV mobile
remote app uses) for launching deep links, sending key events, reading the
foreground app, and power state. Optionally uses `pychromecast` to read real
media playback state, which the remote protocol itself does not expose.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from ..models import DEFAULT_REMOTE_API_PORT, DEFAULT_REMOTE_PAIR_PORT, Tuner
from .base import (
    BackendNotPaired,
    BackendUnavailable,
    Capabilities,
    ControlBackend,
    DeviceInfo,
    PlaybackState,
)

logger = logging.getLogger(__name__)

_CLIENT_NAME = "APITuner"


class AndroidTvRemoteBackend(ControlBackend):
    capabilities = Capabilities(
        keys=True,
        current_app=True,
        playback_state=True,  # best-effort via pychromecast
        power=True,
        app_list=False,
        install=False,
    )

    def __init__(self, tuner: Tuner, certs_dir: Path) -> None:
        self._tuner = tuner
        self._host = tuner.control.host
        self._api_port = tuner.control.port or DEFAULT_REMOTE_API_PORT
        self._pair_port = tuner.control.pair_port or DEFAULT_REMOTE_PAIR_PORT
        self._certfile = str(certs_dir / f"{tuner.id}.crt")
        self._keyfile = str(certs_dir / f"{tuner.id}.key")
        self._remote: Any = None
        self._connected = False
        self._cast: Any = None
        self._lock = asyncio.Lock()

    # -- Connection lifecycle --

    def _build_remote(self) -> Any:
        from androidtvremote2 import AndroidTVRemote

        return AndroidTVRemote(
            client_name=_CLIENT_NAME,
            certfile=self._certfile,
            keyfile=self._keyfile,
            host=self._host,
            api_port=self._api_port,
            pair_port=self._pair_port,
            enable_ime=True,  # required for current_app tracking
        )

    async def connect(self) -> None:
        from androidtvremote2 import CannotConnect, InvalidAuth

        async with self._lock:
            if self._connected and self._remote is not None:
                return
            if self._remote is None:
                self._remote = self._build_remote()
            await self._remote.async_generate_cert_if_missing()
            try:
                await self._remote.async_connect()
            except InvalidAuth as exc:
                raise BackendNotPaired(
                    f"Tuner {self._tuner.name!r} is not paired"
                ) from exc
            except CannotConnect as exc:
                raise BackendUnavailable(
                    f"Cannot reach {self._host}: {exc}"
                ) from exc
            self._remote.keep_reconnecting()
            self._connected = True

    async def close(self) -> None:
        async with self._lock:
            if self._remote is not None:
                try:
                    self._remote.disconnect()
                except Exception:  # noqa: BLE001 - best effort teardown
                    pass
            self._connected = False
        if self._cast is not None:
            await asyncio.get_event_loop().run_in_executor(None, self._close_cast)

    async def health(self) -> bool:
        try:
            await self.connect()
        except BackendNotPaired:
            return False
        except Exception:  # noqa: BLE001
            return False
        return bool(self._connected)

    async def get_info(self) -> DeviceInfo:
        await self.connect()
        info = self._remote.device_info or {}
        return DeviceInfo(
            model=info.get("model"),
            manufacturer=info.get("manufacturer"),
            os_version=info.get("sw_version"),
            sdk_int=None,  # not exposed by the remote protocol
            packages=[],  # remote protocol can't enumerate installed apps
        )

    # -- Control primitives --

    async def launch(
        self,
        *,
        package: str,
        deeplink: Optional[str] = None,
        component: Optional[str] = None,  # unused: URL/intent resolution handles it
        action: Optional[str] = None,
        extras: Optional[str] = None,
    ) -> None:
        await self.connect()
        target = deeplink if deeplink else package
        self._remote.send_launch_app_command(target)

    async def send_key(self, key: str) -> None:
        await self.connect()
        self._remote.send_key_command(key)

    async def current_app(self) -> Optional[str]:
        await self.connect()
        return self._remote.current_app

    async def stop(self) -> None:
        # HOME returns to the Google TV launcher, which stops playback.
        await self.connect()
        self._remote.send_key_command("HOME")

    async def playback_state(self) -> PlaybackState:
        try:
            state = await asyncio.get_event_loop().run_in_executor(
                None, self._get_cast_player_state
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Cast playback probe failed for %s: %s", self._host, exc)
            return PlaybackState.UNKNOWN
        if state is None:
            return PlaybackState.UNKNOWN
        mapping = {
            "PLAYING": PlaybackState.PLAYING,
            "BUFFERING": PlaybackState.PLAYING,
            "PAUSED": PlaybackState.PAUSED,
            "IDLE": PlaybackState.IDLE,
        }
        return mapping.get(state, PlaybackState.UNKNOWN)

    # -- Pairing --

    @property
    def requires_pairing(self) -> bool:
        return True

    async def is_paired(self) -> bool:
        from androidtvremote2 import InvalidAuth

        try:
            await self.connect()
        except BackendNotPaired:
            return False
        except InvalidAuth:
            return False
        except Exception:  # noqa: BLE001 - unreachable != unpaired, but treat cautiously
            return False
        return True

    async def start_pairing(self) -> None:
        async with self._lock:
            # Fresh remote for pairing so we aren't mid-reconnect.
            self._connected = False
            self._remote = self._build_remote()
            await self._remote.async_generate_cert_if_missing()
            await self._remote.async_start_pairing()

    async def finish_pairing(self, pin: str) -> None:
        if self._remote is None:
            raise BackendUnavailable("Pairing was not started")
        await self._remote.async_finish_pairing(pin.strip())
        await self._remote.async_connect()
        self._remote.keep_reconnecting()
        self._connected = True

    # -- pychromecast helpers (blocking; run in executor) --

    def _ensure_cast(self) -> Any:
        if self._cast is not None:
            return self._cast
        import pychromecast

        chromecasts, browser = pychromecast.get_chromecasts(timeout=8)
        try:
            for cc in chromecasts:
                host = getattr(cc.cast_info, "host", None)
                if host == self._host:
                    cc.wait(timeout=5)
                    self._cast = cc
                    break
            else:
                for cc in chromecasts:
                    try:
                        cc.disconnect(blocking=False)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            pychromecast.discovery.stop_discovery(browser)
        return self._cast

    def _get_cast_player_state(self) -> Optional[str]:
        cast = self._ensure_cast()
        if cast is None:
            return None
        status = cast.media_controller.status
        return getattr(status, "player_state", None)

    def _close_cast(self) -> None:
        try:
            self._cast.disconnect(blocking=False)
        except Exception:  # noqa: BLE001
            pass
        self._cast = None
