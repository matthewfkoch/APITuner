"""Split launch (Agent) vs keys (remote/ADB) for hybrid tuners."""

from __future__ import annotations

from typing import Any, Optional

from .base import Capabilities, ControlBackend, DeviceInfo, PlaybackState


class SplitControlBackend(ControlBackend):
    """Launch/probes on ``launch``; D-pad / shell keys on ``keys`` when present."""

    def __init__(self, launch: ControlBackend, keys: ControlBackend) -> None:
        self._launch = launch
        self._keys = keys
        lc = launch.capabilities
        kc = keys.capabilities
        self.capabilities = Capabilities(
            keys=bool(kc.keys or lc.keys),
            dpad=bool(kc.dpad or lc.dpad),
            shell=bool(kc.shell or lc.shell),
            current_app=bool(lc.current_app or kc.current_app),
            playback_state=bool(lc.playback_state or kc.playback_state),
            power=bool(lc.power or kc.power),
            app_list=bool(lc.app_list or kc.app_list),
            install=bool(lc.install or kc.install),
        )

    @property
    def launch_backend(self) -> ControlBackend:
        return self._launch

    @property
    def keys_backend(self) -> ControlBackend:
        return self._keys

    async def connect(self) -> None:
        await self._launch.connect()
        try:
            await self._keys.connect()
        except Exception:  # noqa: BLE001
            pass

    async def close(self) -> None:
        # Only close owned sides when manager invalidates each cache separately.
        return None

    async def health(self) -> bool:
        return await self._launch.health()

    async def get_info(self) -> DeviceInfo:
        return await self._launch.get_info()

    async def launch(
        self,
        *,
        package: str,
        deeplink: Optional[str] = None,
        component: Optional[str] = None,
        action: Optional[str] = None,
        extras: Optional[str] = None,
    ) -> None:
        await self._launch.launch(
            package=package,
            deeplink=deeplink,
            component=component,
            action=action,
            extras=extras,
        )

    async def send_key(self, key: str) -> None:
        await self._keys.send_key(key)

    async def current_app(self) -> Optional[str]:
        if self._launch.capabilities.current_app:
            return await self._launch.current_app()
        return await self._keys.current_app()

    async def playback_state(self) -> PlaybackState:
        if self._launch.capabilities.playback_state:
            return await self._launch.playback_state()
        return await self._keys.playback_state()

    async def stop(self) -> None:
        await self._launch.stop()

    async def force_stop(self, package: str) -> None:
        for be in (self._keys, self._launch):
            fn = getattr(be, "force_stop", None)
            if callable(fn):
                await fn(package)
                return
        await self._launch.stop()

    async def run_shell(self, command: str) -> str:
        for be in (self._keys, self._launch):
            if getattr(be.capabilities, "shell", False) and hasattr(be, "run_shell"):
                return await be.run_shell(command)  # type: ignore[no-any-return]
        raise NotImplementedError("No shell-capable backend in hybrid split")

    @property
    def requires_pairing(self) -> bool:
        return self._keys.requires_pairing

    async def is_paired(self) -> bool:
        return await self._keys.is_paired()

    async def start_pairing(self) -> None:
        await self._keys.start_pairing()

    async def finish_pairing(self, pin: str) -> None:
        await self._keys.finish_pairing(pin)

    @property
    def client_token(self) -> Any:
        return getattr(self._keys, "client_token", None)

    async def get_live_capabilities(self) -> dict:
        getter = getattr(self._launch, "get_live_capabilities", None)
        live: dict = {}
        if getter is not None:
            try:
                raw = await getter()
                if isinstance(raw, dict):
                    live = dict(raw)
            except Exception:  # noqa: BLE001
                pass
        # Keys plane supplies real D-pad even when Agent reports keys=False for DPAD.
        live["keys"] = True if self._keys.capabilities.keys else live.get("keys", False)
        live["dpad"] = bool(self._keys.capabilities.dpad or live.get("dpad", False))
        live["shell"] = bool(self._keys.capabilities.shell or live.get("shell", False))
        return live
