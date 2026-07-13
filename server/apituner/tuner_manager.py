"""Tuner pool + capability-aware tune orchestration."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .backends import ControlBackend, DeviceInfo, PlaybackState, build_backend
from .config import ConfigStore
from .models import Channel, GlobalOptions, Tuner

logger = logging.getLogger(__name__)


class NoTunerAvailable(Exception):
    """No eligible/free tuner could serve the channel."""


class TuneFailed(Exception):
    """The device failed to reach a playable state in time."""


def _new_tune_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class TunerState:
    tuner_id: str
    locked: bool = False
    tune_id: Optional[str] = None
    channel_number: Optional[int] = None
    channel_name: Optional[str] = None
    lock_obtained: float = 0.0
    last_seen: float = 0.0
    bytes_transferred: int = 0
    last_tune_seconds: Optional[float] = None
    last_error: Optional[str] = None


@dataclass
class Lease:
    tuner: Tuner
    backend: ControlBackend
    tune_id: str
    channel: Channel


class TunerManager:
    def __init__(self, store: ConfigStore) -> None:
        self._store = store
        self._backends: dict[str, ControlBackend] = {}
        self._info: dict[str, DeviceInfo] = {}
        self._states: dict[str, TunerState] = {}
        self._alloc_lock = asyncio.Lock()
        self._reaper_task: Optional[asyncio.Task] = None

    # -- Config helpers --

    @property
    def _options(self) -> GlobalOptions:
        return self._store.config.options

    @property
    def options(self) -> GlobalOptions:
        return self._store.config.options

    def _tuners(self) -> list[Tuner]:
        return self._store.config.tuners

    def _tuner(self, tuner_id: str) -> Optional[Tuner]:
        return next((t for t in self._tuners() if t.id == tuner_id), None)

    def get_backend(self, tuner: Tuner) -> ControlBackend:
        backend = self._backends.get(tuner.id)
        if backend is None:
            backend = build_backend(
                tuner, self._store.certs_dir, request_timeout=self._options.request_timeout
            )
            self._backends[tuner.id] = backend
        return backend

    def _state(self, tuner_id: str) -> TunerState:
        st = self._states.get(tuner_id)
        if st is None:
            st = TunerState(tuner_id=tuner_id)
            self._states[tuner_id] = st
        return st

    async def invalidate(self, tuner_id: str) -> None:
        """Drop cached backend/info for a tuner (after edit/removal)."""
        backend = self._backends.pop(tuner_id, None)
        self._info.pop(tuner_id, None)
        self._states.pop(tuner_id, None)
        if backend is not None:
            try:
                await backend.close()
            except Exception:  # noqa: BLE001
                pass

    # -- Info / health --

    async def refresh_info(self, tuner_id: str) -> Optional[DeviceInfo]:
        tuner = self._tuner(tuner_id)
        if tuner is None:
            return None
        backend = self.get_backend(tuner)
        try:
            info = await backend.get_info()
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_info failed for %s: %s", tuner.name, exc)
            return self._info.get(tuner_id)
        self._info[tuner_id] = info
        return info

    async def health(self, tuner_id: str) -> bool:
        tuner = self._tuner(tuner_id)
        if tuner is None:
            return False
        try:
            return await self.get_backend(tuner).health()
        except Exception:  # noqa: BLE001
            return False

    # -- Selection --

    def _has_app(self, tuner_id: str, channel: Channel) -> Optional[bool]:
        info = self._info.get(tuner_id)
        if not info or not info.packages:
            return None  # unknown
        if channel.package_name in info.packages:
            return True
        if channel.alternate_package_name and channel.alternate_package_name in info.packages:
            return True
        return False

    def _choose_package(self, tuner_id: str, channel: Channel) -> str:
        info = self._info.get(tuner_id)
        if info and info.packages:
            if channel.package_name in info.packages:
                return channel.package_name
            if (
                channel.alternate_package_name
                and channel.alternate_package_name in info.packages
            ):
                return channel.alternate_package_name
        return channel.package_name

    async def _select(self, channel: Channel, exclude: set[str]) -> Optional[Tuner]:
        async with self._alloc_lock:
            candidates: list[Tuner] = []
            for tuner in self._tuners():
                if tuner.id in exclude or not tuner.enabled:
                    continue
                if self._state(tuner.id).locked:
                    continue
                if self._has_app(tuner.id, channel) is False:
                    continue
                candidates.append(tuner)
            # Prefer tuners known to have the app installed.
            candidates.sort(key=lambda t: 0 if self._has_app(t.id, channel) else 1)
            if not candidates:
                return None
            chosen = candidates[0]
            st = self._state(chosen.id)
            st.locked = True
            st.lock_obtained = time.time()
            st.last_seen = time.time()
            return chosen

    def _unlock(self, tuner_id: str) -> None:
        st = self._state(tuner_id)
        st.locked = False
        st.tune_id = None
        st.channel_number = None
        st.channel_name = None
        st.bytes_transferred = 0

    # -- Tune orchestration --

    async def lease(self, channel: Channel) -> Lease:
        options = self._options
        tried: set[str] = set()
        last_err: Optional[Exception] = None
        while True:
            tuner = await self._select(channel, exclude=tried)
            if tuner is None:
                if last_err is not None:
                    raise TuneFailed(str(last_err))
                raise NoTunerAvailable(
                    f"No free tuner can serve channel {channel.number}"
                )
            tried.add(tuner.id)
            backend = self.get_backend(tuner)
            tune_id = _new_tune_id()
            try:
                await self._do_tune(tuner, backend, channel, tune_id, options)
                return Lease(tuner=tuner, backend=backend, tune_id=tune_id, channel=channel)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tune %s failed on %s: %s", tune_id, tuner.name, exc)
                st = self._state(tuner.id)
                st.last_error = str(exc)
                self._unlock(tuner.id)
                last_err = exc
                if not options.retry_on_other_tuner:
                    raise

    async def _do_tune(
        self,
        tuner: Tuner,
        backend: ControlBackend,
        channel: Channel,
        tune_id: str,
        options: GlobalOptions,
    ) -> None:
        loop = asyncio.get_event_loop()
        t0 = loop.time()

        if channel.compatibility_mode:
            try:
                await backend.stop()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(1.0)

        chosen_pkg = self._choose_package(tuner.id, channel)
        prior_app: Optional[str] = None
        if backend.capabilities.current_app:
            try:
                prior_app = await backend.current_app()
            except Exception:  # noqa: BLE001
                pass

        await backend.launch(
            package=chosen_pkg,
            deeplink=channel.url or None,
            component=channel.component,
            action=channel.action,
            extras=channel.extra_string,
        )
        launch_at = loop.time()

        deadline = launch_at + options.tune_timeout_seconds
        ready = await self._wait_ready(
            backend,
            channel,
            chosen_pkg,
            options,
            deadline,
            prior_app=prior_app,
            launch_at=launch_at,
        )
        if not ready:
            raise TuneFailed(f"channel {channel.number} not ready within timeout")

        if channel.key_macro and backend.capabilities.keys:
            for key in channel.key_macro:
                try:
                    await backend.send_key(key)
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(0.5)

        elapsed = loop.time() - t0
        st = self._state(tuner.id)
        st.tune_id = tune_id
        st.channel_number = channel.number
        st.channel_name = channel.name
        st.last_tune_seconds = elapsed
        st.last_error = None
        st.last_seen = time.time()
        logger.info(
            "Tune %s ready in %.2fs (%s on %s)",
            tune_id,
            elapsed,
            channel.name,
            tuner.name,
        )

    async def _wait_ready(
        self,
        backend: ControlBackend,
        channel: Channel,
        chosen_pkg: str,
        options: GlobalOptions,
        deadline: float,
        *,
        prior_app: Optional[str] = None,
        launch_at: Optional[float] = None,
    ) -> bool:
        loop = asyncio.get_event_loop()
        caps = backend.capabilities
        targets = {chosen_pkg, channel.package_name}
        if channel.alternate_package_name:
            targets.add(channel.alternate_package_name)

        launch_at = launch_at or loop.time()
        same_app_switch = (
            prior_app is not None
            and prior_app in targets
            and bool(channel.url)
        )
        same_app_ready_delay = 2.0

        use_playback = options.wait_for_playback and caps.playback_state
        playback_unknown_since: Optional[float] = None
        playback_idle_since: Optional[float] = None

        # No readiness signal at all: fixed short delay then accept.
        if not use_playback and not caps.current_app:
            await asyncio.sleep(min(3.0, max(0.0, deadline - loop.time())))
            return True

        while loop.time() < deadline:
            # In-app channel changes do not emit a fresh foreground event.
            if same_app_switch and loop.time() - launch_at >= same_app_ready_delay:
                return True

            if use_playback:
                ps = await backend.playback_state()
                if ps == PlaybackState.PLAYING:
                    return True
                if ps == PlaybackState.UNKNOWN:
                    if playback_unknown_since is None:
                        playback_unknown_since = loop.time()
                    elif loop.time() - playback_unknown_since > 3.0:
                        use_playback = False  # signal never materialized; fall back
                elif ps == PlaybackState.IDLE:
                    playback_unknown_since = None
                    if playback_idle_since is None:
                        playback_idle_since = loop.time()
                    elif loop.time() - playback_idle_since > 3.0:
                        use_playback = False  # buffering between channels; fall back
                else:
                    playback_unknown_since = None
                    playback_idle_since = None

            if caps.current_app:
                app = await backend.current_app()
                if app and app in targets:
                    return True

            await asyncio.sleep(0.75)

        # Final grace: accept if the app is at least foreground.
        if caps.current_app:
            app = await backend.current_app()
            if app and app in targets:
                return True
        if same_app_switch:
            return True
        return False

    # -- Streaming lifecycle --

    def touch(self, tune_id: str, nbytes: int) -> None:
        for st in self._states.values():
            if st.tune_id == tune_id:
                st.last_seen = time.time()
                st.bytes_transferred += nbytes
                return

    async def release(self, lease: Lease) -> None:
        options = self._options
        self._unlock(lease.tuner.id)
        if (
            options.stop_on_release
            or not options.keep_apps_running
            or lease.channel.compatibility_mode
        ):
            try:
                await lease.backend.stop()
            except Exception:  # noqa: BLE001
                pass
        logger.info("Released tuner %s (tune %s)", lease.tuner.name, lease.tune_id)

    # -- Status + reaper --

    def status(self) -> list[dict]:
        out = []
        now = time.time()
        for tuner in self._tuners():
            st = self._state(tuner.id)
            info = self._info.get(tuner.id)
            out.append(
                {
                    "id": tuner.id,
                    "name": tuner.name,
                    "backend": tuner.control.type,
                    "enabled": tuner.enabled,
                    "locked": st.locked,
                    "tune_id": st.tune_id,
                    "channel_number": st.channel_number,
                    "channel_name": st.channel_name,
                    "lock_seconds": round(now - st.lock_obtained, 1) if st.locked else None,
                    "last_seen_seconds": round(now - st.last_seen, 1) if st.last_seen else None,
                    "bytes_transferred": st.bytes_transferred,
                    "last_tune_seconds": st.last_tune_seconds,
                    "last_error": st.last_error,
                    "model": info.model if info else None,
                }
            )
        return out

    async def start_reaper(self) -> None:
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._reaper_loop())

    async def stop_reaper(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None
        for backend in list(self._backends.values()):
            try:
                await backend.close()
            except Exception:  # noqa: BLE001
                pass

    async def _reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            timeout = self._options.stuck_tuner_timeout_seconds
            now = time.time()
            for st in list(self._states.values()):
                if st.locked and st.last_seen and (now - st.last_seen) > timeout:
                    logger.warning(
                        "Reaping stuck tuner %s (no data for %.0fs)",
                        st.tuner_id,
                        now - st.last_seen,
                    )
                    self._unlock(st.tuner_id)
