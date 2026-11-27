"""Tuner pool + capability-aware tune orchestration."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .backends import (
    Capabilities,
    ControlBackend,
    DeviceInfo,
    PlaybackState,
    SplitControlBackend,
    build_backend,
    build_keys_backend,
)
from .config import ConfigStore
from .config_interpreter import (
    ConfigInterpreterError,
    resolve_app_play_config,
    resolve_tune_configuration,
    run_commands,
)
from .deeplink_catalog import (
    infer_provider,
    merge_package_try_order,
    packages_for,
    profile_for_device,
)
from .dynamic_url import (
    DynamicUrlError,
    looks_like_dynamic_url,
    resolve_dynamic_url,
)
from .keys import (
    key_requires_dpad,
    normalize_key_macro,
    parse_key_macro_sleep,
    validate_key_macro_waits,
)
from .models import Channel, GlobalOptions, TuneConfiguration, Tuner
from .packages import package_candidates, package_try_order
from .stream import StreamRelay
from .tune_matching import (
    _COLD_START_RESEND_SECONDS,
    _GENERIC_TITLE_TOKENS,
    _HOME_BEFORE_DEEPLINK_SECONDS,
    _NONSTALE_SECOND_RESEND_SECONDS,
    _PLACEHOLDER_TITLES,
    _STALE_FOREGROUND_IDLE_RELAUNCH_SECONDS,
    _STALE_PLAYBACK_PACKAGES,
    _STALE_SPLASH_GRACE_MIN_TIMEOUT,
    _STALE_SPLASH_GRACE_SECONDS,
    _title_is_placeholder,
    _title_looks_like_channel,
    _title_tokens,
    _titles_equivalent,
    tune_not_ready_message,
)
from .tune_readiness import ReadinessTimings, home_off_splash, wait_for_tune_ready
from .whos_watching import clear_whos_watching_prompt

_RELEASE_TIMEOUT_SECONDS = 60.0

__all__ = [
    "Lease",
    "NoTunerAvailable",
    "TuneFailed",
    "TunerInUse",
    "TunerManager",
    "TunerState",
    "tune_not_ready_message",
    "_GENERIC_TITLE_TOKENS",
    "_PLACEHOLDER_TITLES",
    "_title_tokens",
    "_title_is_placeholder",
    "_title_looks_like_channel",
    "_titles_equivalent",
]


async def _backend_launch(backend: ControlBackend, **kwargs) -> None:
    """Call ``launch``, dropping ``clear_task`` on backends that lack the flag."""
    try:
        await backend.launch(**kwargs)
    except TypeError:
        kwargs.pop("clear_task", None)
        await backend.launch(**kwargs)


async def _home_off_splash(backend: ControlBackend) -> None:
    """Best-effort HOME so a stuck splash does not swallow the next VIEW intent."""
    await home_off_splash(backend, delay_seconds=_HOME_BEFORE_DEEPLINK_SECONDS)


logger = logging.getLogger(__name__)


class NoTunerAvailable(Exception):
    """No eligible/free tuner could serve the channel."""


class TunerInUse(Exception):
    """A specific tuner index was requested but is already locked."""


class TuneFailed(Exception):
    """The device failed to reach a playable state in time."""


def _new_tune_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class TunerState:
    tuner_id: str
    locked: bool = False
    tune_id: Optional[str] = None
    channel_number: Optional[str] = None
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
    tune_configuration: Optional[TuneConfiguration] = None
    # Backend used for post_tune key/shell commands (keys plane when hybrid).
    command_backend: Optional[ControlBackend] = None
    # Background App Play / deeplink tune when stream_during_tune is enabled.
    tune_task: Optional[asyncio.Task] = None
    force_stop: bool = False
    player_session_key: Optional[str] = None
    monitor_task: Optional[asyncio.Task] = None


class TunerManager:
    def __init__(self, store: ConfigStore) -> None:
        self._store = store
        self._backends: dict[str, ControlBackend] = {}
        self._keys_backends: dict[str, ControlBackend] = {}
        self._info: dict[str, DeviceInfo] = {}
        self._states: dict[str, TunerState] = {}
        self._alloc_lock = asyncio.Lock()
        self._reaper_task: Optional[asyncio.Task] = None
        self._leases: dict[str, Lease] = {}
        self._release_tasks: dict[str, asyncio.Task] = {}
        self.stream_relay = StreamRelay(self)

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

    def get_keys_backend(self, tuner: Tuner) -> Optional[ControlBackend]:
        if tuner.keys_control is None:
            return None
        backend = self._keys_backends.get(tuner.id)
        if backend is None:
            backend = build_keys_backend(
                tuner, self._store.certs_dir, request_timeout=self._options.request_timeout
            )
            if backend is not None:
                self._keys_backends[tuner.id] = backend
        return backend

    def get_pairing_backend(self, tuner: Tuner) -> ControlBackend:
        """Backend that owns Pair (keys plane when hybrid Agent + remote/Fire)."""
        keys = self.get_keys_backend(tuner)
        if keys is not None and keys.requires_pairing:
            return keys
        return self.get_backend(tuner)

    def get_command_backend(self, tuner: Tuner) -> ControlBackend:
        """Keys / D-pad / App Play plane (hybrid keys_control when set)."""
        primary = self.get_backend(tuner)
        return self._command_backend(tuner, primary)

    def _command_backend(
        self, tuner: Tuner, primary: ControlBackend
    ) -> ControlBackend:
        """Backend for keyevents / App Play scripts (prefer keys plane)."""
        keys = self.get_keys_backend(tuner)
        if keys is None:
            return primary
        return SplitControlBackend(primary, keys)

    def _state(self, tuner_id: str) -> TunerState:
        st = self._states.get(tuner_id)
        if st is None:
            st = TunerState(tuner_id=tuner_id)
            self._states[tuner_id] = st
        return st

    async def invalidate(self, tuner_id: str) -> None:
        """Drop cached backend/info for a tuner (after edit/removal)."""
        backend = self._backends.pop(tuner_id, None)
        keys = self._keys_backends.pop(tuner_id, None)
        self._info.pop(tuner_id, None)
        self._states.pop(tuner_id, None)
        for be in (backend, keys):
            if be is not None:
                try:
                    await be.close()
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

    def enabled_tuners(self) -> list[Tuner]:
        """Enabled tuners in config order (tuner0, tuner1, ... for HDHR)."""
        return [t for t in self._tuners() if t.enabled]

    def tuner_count(self) -> int:
        return len(self.enabled_tuners())

    def tuner_at_index(self, index: int) -> Optional[Tuner]:
        enabled = self.enabled_tuners()
        if index < 0 or index >= len(enabled):
            return None
        return enabled[index]

    def _has_app(self, tuner_id: str, channel: Channel) -> Optional[bool]:
        info = self._info.get(tuner_id)
        if not info or not info.packages:
            return None  # unknown
        candidates = package_candidates(
            channel.package_name, channel.alternate_package_name
        )
        if any(pkg in info.packages for pkg in candidates):
            return True
        return False

    def _package_try_order(self, tuner_id: str, channel: Channel) -> list[str]:
        info = self._info.get(tuner_id)
        installed = list(info.packages) if info and info.packages else None
        return package_try_order(
            channel.package_name,
            channel.alternate_package_name,
            installed=installed,
        )

    def _choose_package(self, tuner_id: str, channel: Channel) -> str:
        order = self._package_try_order(tuner_id, channel)
        if not order:
            return channel.package_name
        chosen = order[0]
        if chosen != channel.package_name:
            logger.info(
                "Using installed package %s on %s (channel lists %s)",
                chosen,
                tuner_id,
                channel.package_name,
            )
        return chosen

    async def _launch_with_package_fallbacks(
        self,
        backend: ControlBackend,
        channel: Channel,
        packages: list[str],
        *,
        deeplink: Optional[str] = None,
        component: Optional[str] = None,
        action: Optional[str] = None,
        extras: Optional[str] = None,
        clear_task: bool = False,
    ) -> str:
        """Try each package candidate until launch succeeds."""
        errors: list[str] = []
        for pkg in packages:
            try:
                await _backend_launch(
                    backend,
                    package=pkg,
                    deeplink=deeplink,
                    component=component,
                    action=action,
                    extras=extras,
                    clear_task=clear_task,
                )
                if pkg != packages[0]:
                    logger.info(
                        "Launch succeeded with fallback package %s "
                        "(channel lists %s)",
                        pkg,
                        channel.package_name,
                    )
                return pkg
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Launch failed for %s on channel %s: %s",
                    pkg,
                    channel.number,
                    exc,
                )
                errors.append(f"{pkg}: {exc}")
        raise TuneFailed(
            f"Could not open any package for channel {channel.number} "
            f"({channel.name}): " + "; ".join(errors)
        )

    def _tuner_supports_dpad(self, tuner: Tuner) -> bool:
        """True if primary or keys_control can inject D-pad (App Play / macros)."""
        keys = tuner.keys_control.type if tuner.keys_control else None
        kind = keys or tuner.control.type
        return kind in ("androidtv_remote", "firetv_rest", "adb")

    def _app_play_backend_rank(self, tuner: Tuner) -> int:
        """Lower is better. Order comes from ``options.app_play_prefer``."""
        prefer = self._options.app_play_prefer
        if prefer == "any":
            return 0
        keys = tuner.keys_control.type if tuner.keys_control else None
        kind = keys or tuner.control.type
        if prefer == "fire":
            order = ("adb", "firetv_rest", "androidtv_remote")
        else:  # google_tv
            order = ("androidtv_remote", "adb", "firetv_rest")
        try:
            return order.index(kind)
        except ValueError:
            return len(order)

    def _channel_needs_dpad(self, channel: Channel) -> bool:
        configs = self._store.config.configurations
        if resolve_app_play_config(channel, configs) is not None:
            return True
        if any(key_requires_dpad(key) for key in channel.key_macro or ()):
            return True
        try:
            overlay = resolve_tune_configuration(channel, configs)
        except ConfigInterpreterError:
            overlay = None
        if overlay is not None and overlay.global_options.check_for_and_clear_whos_watching_prompts:
            return True
        return False

    async def _select(self, channel: Channel, exclude: set[str]) -> Optional[Tuner]:
        needs_dpad = self._channel_needs_dpad(channel)
        app_play = needs_dpad and resolve_app_play_config(
            channel, self._store.config.configurations
        ) is not None
        async with self._alloc_lock:
            candidates: list[Tuner] = []
            for tuner in self._tuners():
                if channel.source == "fastchannels" and tuner.control.type != "http_agent":
                    continue
                if tuner.id in exclude or not tuner.enabled:
                    continue
                if self._state(tuner.id).locked:
                    continue
                if self._has_app(tuner.id, channel) is False:
                    continue
                if needs_dpad and not self._tuner_supports_dpad(tuner):
                    continue
                candidates.append(tuner)
            # Prefer known-installed app, then Fire/ADB App Play paths, then
            # other D-pad backends (Chromecast Remote last for App Play).
            candidates.sort(
                key=lambda t: (
                    0 if self._has_app(t.id, channel) else 1,
                    self._app_play_backend_rank(t) if app_play else 0,
                    0 if self._tuner_supports_dpad(t) else 1,
                )
            )
            if not candidates:
                return None
            chosen = candidates[0]
            st = self._state(chosen.id)
            st.locked = True
            st.lock_obtained = time.time()
            st.last_seen = time.time()
            return chosen

    async def _select_index(self, channel: Channel, tuner_index: int) -> Tuner:
        """Lock a specific enabled-tuner index, or raise TunerInUse / NoTunerAvailable."""
        async with self._alloc_lock:
            tuner = self.tuner_at_index(tuner_index)
            if tuner is None:
                raise NoTunerAvailable(f"No tuner at index {tuner_index}")
            if channel.source == "fastchannels" and tuner.control.type != "http_agent":
                raise NoTunerAvailable("FastChannels requires an HTTP Agent tuner")
            if self._has_app(tuner.id, channel) is False:
                raise NoTunerAvailable(
                    f"Tuner {tuner_index} cannot serve channel {channel.number}"
                )
            st = self._state(tuner.id)
            if st.locked:
                raise TunerInUse(f"Tuner {tuner_index} is in use")
            st.locked = True
            st.lock_obtained = time.time()
            st.last_seen = time.time()
            return tuner

    def _unlock(self, tuner_id: str) -> None:
        st = self._state(tuner_id)
        st.locked = False
        st.tune_id = None
        st.channel_number = None
        st.channel_name = None
        st.bytes_transferred = 0

    # -- Tune orchestration --

    async def lease(
        self, channel: Channel, *, tuner_index: Optional[int] = None
    ) -> Lease:
        options = self._options
        if not channel.available:
            raise NoTunerAvailable("Channel is no longer available from its source")
        if tuner_index is not None:
            tuner = await self._select_index(channel, tuner_index)
            backend = self.get_backend(tuner)
            tune_id = _new_tune_id()
            self._state(tuner.id).tune_id = tune_id
            try:
                lease = await self._lease_on(tuner, backend, channel, tune_id, options)
                self._leases[tune_id] = lease
                return lease
            except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001
                logger.warning("Tune %s failed on %s: %s", tune_id, tuner.name, exc)
                st = self._state(tuner.id)
                st.last_error = str(exc)
                await self._abort_tune(tuner, backend, channel, tune_id)
                raise

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
            self._state(tuner.id).tune_id = tune_id
            try:
                lease = await self._lease_on(tuner, backend, channel, tune_id, options)
                self._leases[tune_id] = lease
                return lease
            except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001
                logger.warning("Tune %s failed on %s: %s", tune_id, tuner.name, exc)
                st = self._state(tuner.id)
                st.last_error = str(exc)
                await self._abort_tune(tuner, backend, channel, tune_id)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                last_err = exc
                if not options.retry_on_other_tuner:
                    raise

    async def _abort_tune(self, tuner, backend, channel, tune_id) -> None:
        lease = self._leases.get(tune_id) or Lease(tuner=tuner, backend=backend, channel=channel,
                      tune_id=tune_id, force_stop=True)
        self._leases[tune_id] = lease
        await self.release(lease)

    async def _lease_on(
        self,
        tuner: Tuner,
        backend: ControlBackend,
        channel: Channel,
        tune_id: str,
        options: GlobalOptions,
    ) -> Lease:
        if channel.source == "fastchannels":
            return await self._lease_fastchannels(tuner, backend, channel, tune_id, options)
        configs = self._store.config.configurations
        try:
            overlay = resolve_tune_configuration(channel, configs)
        except ConfigInterpreterError as exc:
            raise TuneFailed(str(exc)) from exc
        app_play = resolve_app_play_config(channel, configs)
        cmd_backend = self._command_backend(tuner, backend)
        tune_configuration = app_play or overlay

        # Mark the lock as this channel immediately so status/reaper see activity
        # while App Play navigates (stream_during_tune).
        st = self._state(tuner.id)
        st.tune_id = tune_id
        st.channel_number = channel.number
        st.channel_name = channel.name
        st.last_seen = time.time()
        st.bytes_transferred = 0
        st.last_error = None

        # Long App Play / deeplink waits exceed Channels' ~30s HDHR connect
        # timeout unless the encoder stream starts before tune finishes.
        if options.stream_during_tune and app_play is not None:
            # Fail *before* returning a lease so retry_on_other_tuner can pick a
            # D-pad tuner. Streaming first then failing looks like an instant
            # Channels disconnect.
            caps = await self._effective_capabilities(cmd_backend)
            if not caps.dpad:
                raise TuneFailed(
                    f"App Play channel {channel.number} ({channel.name}) requires a "
                    "D-pad backend (androidtv_remote, firetv_rest, or adb); "
                    f"tuner {tuner.name!r} uses {tuner.control.type}"
                    + (
                        f" (keys_control={tuner.keys_control.type})"
                        if tuner.keys_control
                        else " — set keys_control for hybrid Agent+Remote"
                    )
                )
            logger.info(
                "Tune %s streaming during App Play on %s (%s)",
                tune_id,
                tuner.name,
                channel.name,
            )
            task = asyncio.create_task(
                self._do_tune_background(
                    tuner,
                    backend,
                    channel,
                    tune_id,
                    options,
                    app_play=app_play,
                    overlay=None,
                    command_backend=cmd_backend,
                ),
                name=f"tune-{tune_id}",
            )
            return Lease(
                tuner=tuner,
                backend=backend,
                tune_id=tune_id,
                channel=channel,
                tune_configuration=tune_configuration,
                command_backend=cmd_backend,
                tune_task=task,
            )

        # Deeplink playback waits (incl. one relaunch) can also burn the connect
        # window; stream early when we will wait for MediaSession PLAYING.
        if (
            options.stream_during_tune
            and options.wait_for_playback
            and app_play is None
        ):
            logger.info(
                "Tune %s streaming during deeplink wait on %s (%s)",
                tune_id,
                tuner.name,
                channel.name,
            )
            task = asyncio.create_task(
                self._do_tune_background(
                    tuner,
                    backend,
                    channel,
                    tune_id,
                    options,
                    app_play=None,
                    overlay=overlay,
                    command_backend=cmd_backend,
                ),
                name=f"tune-{tune_id}",
            )
            return Lease(
                tuner=tuner,
                backend=backend,
                tune_id=tune_id,
                channel=channel,
                tune_configuration=tune_configuration,
                command_backend=cmd_backend,
                tune_task=task,
            )

        await self._do_tune(
            tuner,
            backend,
            channel,
            tune_id,
            options,
            app_play=app_play,
            overlay=overlay if app_play is None else None,
            command_backend=cmd_backend,
        )
        return Lease(
            tuner=tuner,
            backend=backend,
            tune_id=tune_id,
            channel=channel,
            tune_configuration=tune_configuration,
            command_backend=cmd_backend,
        )

    async def _lease_fastchannels(self, tuner, backend, channel, tune_id, options):
        from .fastchannels_runtime import tune, monitor
        from .fastchannels_probe import ProbeError
        if not channel.fastchannels_source or not channel.fastchannels_channel_id:
            raise TuneFailed("FastChannels channel identity is missing; sync again")
        state = self._state(tuner.id)
        state.tune_id, state.channel_number, state.channel_name = tune_id, channel.number, channel.name
        state.last_seen, state.last_error = time.time(), None
        key = f"apituner:{tune_id}:{uuid.uuid4().hex}"
        lease = Lease(tuner=tuner, backend=backend, channel=channel, tune_id=tune_id,
                      player_session_key=key)
        self._leases[tune_id] = lease
        async def launch():
            try:
                await tune(backend, channel, options, key)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc) if isinstance(exc, ProbeError) else "FastChannels tune failed"
                state.last_error = message
                raise TuneFailed(message) from None
        lease.tune_task = asyncio.create_task(launch(), name=f"fastchannels-{tune_id}")
        if not options.stream_during_tune:
            await lease.tune_task
        lease.monitor_task = asyncio.create_task(monitor(backend, lease.tune_task))
        return lease

    async def _do_tune_background(
        self,
        tuner: Tuner,
        backend: ControlBackend,
        channel: Channel,
        tune_id: str,
        options: GlobalOptions,
        *,
        app_play: Optional[TuneConfiguration] = None,
        overlay: Optional[TuneConfiguration] = None,
        command_backend: Optional[ControlBackend] = None,
    ) -> None:
        """Run ``_do_tune`` for stream_during_tune; record failures on tuner state."""
        try:
            await self._do_tune(
                tuner,
                backend,
                channel,
                tune_id,
                options,
                app_play=app_play,
                overlay=overlay,
                command_backend=command_backend,
            )
        except asyncio.CancelledError:
            logger.info("Tune %s cancelled on %s", tune_id, tuner.name)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Tune %s failed during stream on %s: %s", tune_id, tuner.name, exc
            )
            st = self._state(tuner.id)
            st.last_error = str(exc)
            raise

    async def _do_tune(
        self,
        tuner: Tuner,
        backend: ControlBackend,
        channel: Channel,
        tune_id: str,
        options: GlobalOptions,
        *,
        app_play: Optional[TuneConfiguration] = None,
        overlay: Optional[TuneConfiguration] = None,
        command_backend: Optional[ControlBackend] = None,
    ) -> None:
        try:
            validate_key_macro_waits(normalize_key_macro(channel.key_macro))
        except ValueError as exc:
            raise TuneFailed(f"Channel {channel.number} ({channel.name}): {exc}") from exc

        loop = asyncio.get_event_loop()
        t0 = loop.time()
        cmd_be = command_backend or backend

        if channel.compatibility_mode:
            try:
                await backend.stop()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(1.0)

        # Refresh installed-app list before package pick (Agent Usage Access).
        try:
            self._info[tuner.id] = await backend.get_info()
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_info before tune failed for %s: %s", tuner.name, exc)

        try_order = self._package_try_order(tuner.id, channel)
        chosen_pkg = try_order[0] if try_order else channel.package_name
        logger.info(
            "Tune %s package try-order on %s: %s (channel package=%s)",
            tune_id,
            tuner.name,
            try_order,
            channel.package_name,
        )

        if app_play is not None:
            info = self._info.get(tuner.id)
            if info and info.packages:
                if not any(p in info.packages for p in try_order):
                    raise TuneFailed(
                        f"None of {try_order} are installed on {tuner.name}. "
                        "For ESPN App Play use com.espn.gtv (Fire / Amazon) or "
                        "com.espn.score_center (many Google / Android TV builds); "
                        "set package_name and alternate_package_name on the channel."
                    )
            chosen_pkg = await self._do_app_play_tune(
                tuner,
                backend,
                cmd_be,
                channel,
                chosen_pkg,
                options,
                app_play,
                package_fallbacks=try_order,
            )
        else:
            chosen_pkg = await self._do_deeplink_tune(
                tuner,
                backend,
                cmd_be,
                channel,
                chosen_pkg,
                options,
                overlay=overlay,
                package_fallbacks=try_order,
            )

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

    async def _resolve_launch_url(self, channel: Channel) -> str:
        url = channel.url if channel.url is not None else ""
        if not looks_like_dynamic_url(url):
            return url
        try:
            timeout = float(self._options.dynamic_url_timeout)
            attempts = int(self._options.dynamic_url_attempts)
            return await resolve_dynamic_url(
                url, timeout=timeout, attempts=attempts
            )
        except DynamicUrlError as exc:
            raise TuneFailed(str(exc)) from exc

    async def _do_deeplink_tune(
        self,
        tuner: Tuner,
        backend: ControlBackend,
        cmd_backend: ControlBackend,
        channel: Channel,
        chosen_pkg: str,
        options: GlobalOptions,
        *,
        overlay: Optional[TuneConfiguration],
        package_fallbacks: Optional[list[str]] = None,
    ) -> str:
        launch_url = await self._resolve_launch_url(channel)
        fallbacks = package_fallbacks or [chosen_pkg]
        info = self._info.get(tuner.id)
        profile = profile_for_device(
            manufacturer=info.manufacturer if info else None,
            model=info.model if info else None,
            packages=info.packages if info else None,
            control_type=tuner.control.type,
            keys_type=tuner.keys_control.type if tuner.keys_control else None,
            fallback=options.fruitdeeplinks_profile,
        )
        inferred = packages_for(infer_provider(launch_url), profile=profile)
        fallbacks = merge_package_try_order(fallbacks, inferred)

        package_only = not (
            (launch_url and str(launch_url).strip())
            or (channel.component and str(channel.component).strip())
            or (channel.extra_string and str(channel.extra_string).strip())
            or (
                channel.action
                and channel.action
                not in (
                    "android.intent.action.VIEW",
                    "android.intent.action.MAIN",
                )
            )
        )
        if package_only:
            await self._wake_device(cmd_backend)

        remote_foreground = (
            cmd_backend.keys_backend
            if package_only
            and tuner.keys_control is not None
            and tuner.keys_control.type == "androidtv_remote"
            and isinstance(cmd_backend, SplitControlBackend)
            and cmd_backend.keys_backend.capabilities.current_app
            else None
        )

        if overlay is not None and overlay.pre_tune_commands:
            try:
                await run_commands(
                    cmd_backend,
                    overlay.pre_tune_commands,
                    package=chosen_pkg,
                    identifier=launch_url,
                    skip_am_start=True,
                    package_fallbacks=fallbacks,
                )
            except ConfigInterpreterError as exc:
                raise TuneFailed(str(exc)) from exc

        prior_app: Optional[str] = None
        foreground_source = remote_foreground or backend
        if foreground_source.capabilities.current_app:
            try:
                prior_app = await foreground_source.current_app()
            except Exception:  # noqa: BLE001
                pass

        leftover_title: Optional[str] = None
        leftover_state: Optional[PlaybackState] = None
        snap_fn = getattr(backend, "playback_snapshot", None)
        if callable(snap_fn):
            try:
                leftover_state, _pkg0, leftover_title = await snap_fn()
                if leftover_title is not None:
                    leftover_title = str(leftover_title).strip() or None
            except Exception:  # noqa: BLE001
                leftover_title = None
                leftover_state = None

        stale_pkg = any(p in _STALE_PLAYBACK_PACKAGES for p in fallbacks)
        cold_start = not (
            prior_app is not None
            and prior_app in set(fallbacks)
            and bool(launch_url)
        )
        # App already open on splash (no MediaSession): HOME then treat as
        # cold start so the VIEW intent is not delivered onto the splash.
        stuck_splash = (
            stale_pkg
            and prior_app is not None
            and prior_app in set(fallbacks)
            and leftover_state != PlaybackState.PLAYING
        )
        if stuck_splash and not package_only:
            logger.info(
                "HOME before deeplink on %s — %s open but not playing",
                tuner.name,
                prior_app,
            )
            await _home_off_splash(backend)
            cold_start = True
            prior_app = None

        chosen_pkg = await self._launch_with_package_fallbacks(
            backend,
            channel,
            fallbacks,
            deeplink=launch_url or None,
            component=channel.component,
            action=channel.action,
            extras=channel.extra_string,
            clear_task=stale_pkg and cold_start,
        )
        launch_at = asyncio.get_event_loop().time()

        # Compatibility / FDL tune_commands are usually another am start — skip;
        # still allow non-start commands if present.
        if overlay is not None and overlay.tune_commands:
            try:
                await run_commands(
                    cmd_backend,
                    overlay.tune_commands,
                    package=chosen_pkg,
                    identifier=launch_url,
                    skip_am_start=True,
                    package_fallbacks=fallbacks,
                )
            except ConfigInterpreterError as exc:
                raise TuneFailed(str(exc)) from exc

        deadline = launch_at + options.tune_timeout_seconds

        async def _relaunch_deeplink() -> None:
            logger.info(
                "Re-sending deeplink for channel %s (%s) on %s after no playback",
                channel.number,
                channel.name,
                tuner.name,
            )
            if stale_pkg:
                await _home_off_splash(backend)
            await _backend_launch(
                backend,
                package=chosen_pkg,
                deeplink=launch_url or None,
                component=channel.component,
                action=channel.action,
                extras=channel.extra_string,
                clear_task=stale_pkg,
            )

        relaunch: Optional[Callable[[], Awaitable[None]]] = None
        if (
            options.deeplink_relaunch_seconds > 0
            and (launch_url or "").strip()
        ):
            relaunch = _relaunch_deeplink

        not_ready: list[str] = []
        ready = await self._wait_ready(
            backend,
            channel,
            chosen_pkg,
            options,
            deadline,
            prior_app=prior_app,
            launch_at=launch_at,
            relaunch=relaunch,
            leftover_title=leftover_title,
            failure=not_ready,
            foreground_backend=remote_foreground or cmd_backend,
            prefer_foreground_backend=remote_foreground is not None,
        )
        if not ready:
            raise TuneFailed(
                not_ready[0]
                if not_ready
                else f"channel {channel.number} not ready within timeout"
            )

        whos_status: Optional[str] = None
        if overlay is not None and overlay.global_options.check_for_and_clear_whos_watching_prompts:
            whos_status = await self._clear_whos_watching(tuner, cmd_backend, channel)

        if whos_status == "ocr_cleared" and channel.key_macro:
            await asyncio.sleep(3.0)

        await self._send_key_macro(channel, cmd_backend)

        if overlay is not None and overlay.post_playback_start_commands:
            try:
                await run_commands(
                    cmd_backend,
                    overlay.post_playback_start_commands,
                    package=chosen_pkg,
                    identifier=launch_url,
                    skip_am_start=True,
                    package_fallbacks=fallbacks,
                )
            except ConfigInterpreterError as exc:
                raise TuneFailed(str(exc)) from exc
            wait_after = float(
                overlay.global_options.wait_after_post_playback_start_commands_seconds
                or 0
            )
            if wait_after > 0:
                await asyncio.sleep(wait_after)
        return chosen_pkg

    async def _wake_device(self, backend: ControlBackend) -> None:
        """Wake the stick without navigating HOME."""
        try:
            wake = getattr(backend, "wake", None)
            if callable(wake):
                await wake()
            elif hasattr(backend, "run_shell"):
                await backend.run_shell("input keyevent KEYCODE_WAKEUP")  # type: ignore[attr-defined]
            else:
                try:
                    await backend.send_key("WAKEUP")
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("Wake skipped: %s", exc)

    async def _prepare_device_for_app_play(self, backend: ControlBackend) -> None:
        """Wake the stick and return to HOME before App Play D-pad scripts.

        Google TV ambient / screensaver eats directional keys (often lands in
        screensaver settings). Fire is usually fine but HOME is still cheap.
        """
        await self._wake_device(backend)
        try:
            await backend.send_key("HOME")
            await asyncio.sleep(1.0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("App Play HOME skipped: %s", exc)

    async def _do_app_play_tune(
        self,
        tuner: Tuner,
        backend: ControlBackend,
        cmd_backend: ControlBackend,
        channel: Channel,
        chosen_pkg: str,
        options: GlobalOptions,
        app_play: TuneConfiguration,
        *,
        package_fallbacks: Optional[list[str]] = None,
    ) -> str:
        caps = await self._effective_capabilities(cmd_backend)
        if not caps.dpad:
            raise TuneFailed(
                f"App Play channel {channel.number} ({channel.name}) requires a "
                "D-pad backend (androidtv_remote, firetv_rest, or adb); "
                f"tuner {tuner.name!r} uses {tuner.control.type}"
                + (
                    f" (keys_control={tuner.keys_control.type})"
                    if tuner.keys_control
                    else " — set keys_control for hybrid Agent+Remote"
                )
            )

        cfg_opts = app_play.global_options
        identifier = channel.url if channel.url is not None else ""
        fallbacks = package_fallbacks or [chosen_pkg]
        working_pkg = chosen_pkg

        # Screensaver / ambient on Google TV intercepts D-pad (looks like
        # "changing screensaver settings"). Wake + HOME so App Play starts from
        # the launcher, then the config's force-stop / open_app.
        await self._prepare_device_for_app_play(cmd_backend)

        try:
            await run_commands(
                cmd_backend,
                app_play.pre_tune_commands,
                package=chosen_pkg,
                identifier=identifier,
                package_fallbacks=fallbacks,
            )
            opened = await run_commands(
                cmd_backend,
                app_play.tune_commands,
                package=chosen_pkg,
                identifier=identifier,
                package_fallbacks=fallbacks,
            )
            if opened:
                working_pkg = opened
            if app_play.post_playback_start_commands:
                await run_commands(
                    cmd_backend,
                    app_play.post_playback_start_commands,
                    package=working_pkg,
                    identifier=identifier,
                    package_fallbacks=fallbacks,
                )
                wait_after = float(
                    cfg_opts.wait_after_post_playback_start_commands_seconds or 0
                )
                if wait_after > 0:
                    await asyncio.sleep(wait_after)
        except ConfigInterpreterError as exc:
            raise TuneFailed(str(exc)) from exc

        if cfg_opts.check_for_and_clear_whos_watching_prompts:
            await self._clear_whos_watching(tuner, cmd_backend, channel)

        await self._send_key_macro(channel, cmd_backend)

        # Babsonnexus App Play configs typically use fixed delay (no playback probe).
        primary_caps = await self._effective_capabilities(backend)
        if cfg_opts.use_fixed_delay or not (
            options.wait_for_playback and primary_caps.playback_state
        ):
            delay = float(cfg_opts.fixed_delay_seconds or 0)
            if delay <= 0:
                delay = max(1.0, float(options.ready_settle_seconds or 1.0))
            await asyncio.sleep(delay)
            return working_pkg

        loop = asyncio.get_event_loop()
        deadline = loop.time() + options.tune_timeout_seconds
        ready = await self._wait_ready(
            backend,
            channel,
            working_pkg,
            options,
            deadline,
            prior_app=None,
            launch_at=loop.time(),
        )
        if not ready:
            raise TuneFailed(
                f"App Play channel {channel.number} not ready within timeout"
            )
        return working_pkg

    async def _clear_whos_watching(
        self, tuner: Tuner, cmd_backend: ControlBackend, channel: Channel
    ) -> str:
        caps = await self._effective_capabilities(cmd_backend)

        async def _send(key: str) -> None:
            await cmd_backend.send_key(key)

        status = await clear_whos_watching_prompt(
            stream_url=tuner.stream_endpoint,
            send_key=_send,
            has_dpad=bool(caps.dpad),
        )
        logger.info(
            "Who's-watching on %s: %s",
            tuner.name,
            status,
        )
        if status == "skipped_no_dpad":
            raise TuneFailed(
                f"Who's-watching clear on {tuner.name} requires a D-pad keys "
                "backend; set keys_control to androidtv_remote, firetv_rest, or adb"
            )
        if status == "prompt_unverified" and channel.key_macro:
            raise TuneFailed(
                f"Could not verify profile selection on {tuner.name} "
                f"before running the key macro for channel {channel.number}"
            )
        return status

    async def _send_key_macro(
        self, channel: Channel, cmd_backend: ControlBackend
    ) -> None:
        keys = normalize_key_macro(channel.key_macro)
        if not keys:
            return
        real_keys = [k for k in keys if parse_key_macro_sleep(k) is None]
        if real_keys:
            caps = await self._effective_capabilities(cmd_backend)
            needs_dpad = any(key_requires_dpad(k) for k in real_keys)
            if needs_dpad and not caps.dpad:
                raise TuneFailed(
                    f"Channel {channel.number} ({channel.name}) key_macro {keys} "
                    "requires a D-pad keys backend; set keys_control to "
                    "androidtv_remote, firetv_rest, or adb"
                )
            if not caps.keys and not caps.dpad:
                raise TuneFailed(
                    f"Channel {channel.number} ({channel.name}) key_macro requires "
                    "a keys-capable backend"
                )
        for key in keys:
            sleep_sec = parse_key_macro_sleep(key)
            if sleep_sec is not None:
                if sleep_sec > 0:
                    await asyncio.sleep(float(sleep_sec))
                continue
            try:
                await cmd_backend.send_key(key)
            except Exception as exc:  # noqa: BLE001
                raise TuneFailed(
                    f"key_macro failed sending {key!r}: {exc}"
                ) from exc
            await asyncio.sleep(0.5)

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
        relaunch: Optional[Callable[[], Awaitable[None]]] = None,
        leftover_title: Optional[str] = None,
        failure: Optional[list[str]] = None,
        foreground_backend: Optional[ControlBackend] = None,
        prefer_foreground_backend: bool = False,
    ) -> bool:
        caps = await self._effective_capabilities(backend)
        timings = ReadinessTimings(
            stale_splash_grace_seconds=_STALE_SPLASH_GRACE_SECONDS,
            stale_splash_grace_min_timeout=_STALE_SPLASH_GRACE_MIN_TIMEOUT,
            home_before_deeplink_seconds=_HOME_BEFORE_DEEPLINK_SECONDS,
            stale_foreground_idle_relaunch_seconds=_STALE_FOREGROUND_IDLE_RELAUNCH_SECONDS,
            cold_start_resend_seconds=_COLD_START_RESEND_SECONDS,
            nonstale_second_resend_seconds=_NONSTALE_SECOND_RESEND_SECONDS,
        )
        return await wait_for_tune_ready(
            backend=backend,
            caps=caps,
            channel=channel,
            chosen_pkg=chosen_pkg,
            options=options,
            deadline=deadline,
            timings=timings,
            prior_app=prior_app,
            launch_at=launch_at,
            relaunch=relaunch,
            leftover_title=leftover_title,
            failure=failure,
            foreground_backend=foreground_backend,
            prefer_foreground_backend=prefer_foreground_backend,
            home_fn=_home_off_splash,
        )

    async def _effective_capabilities(self, backend: ControlBackend) -> Capabilities:
        """Prefer live Agent permission flags when available; merge hybrid keys."""
        caps = backend.capabilities
        getter = getattr(backend, "get_live_capabilities", None)
        if getter is None:
            return caps
        try:
            live = await getter()
        except Exception:  # noqa: BLE001
            return caps
        if not isinstance(live, dict) or not live:
            return caps
        return Capabilities(
            keys=bool(live.get("keys", caps.keys)),
            dpad=bool(live.get("dpad", caps.dpad)),
            shell=bool(live.get("shell", caps.shell)),
            current_app=bool(live.get("current_app", caps.current_app)),
            playback_state=bool(live.get("playback_state", caps.playback_state)),
            power=bool(live.get("power", caps.power)),
            app_list=bool(live.get("app_list", caps.app_list)),
            install=bool(live.get("install", caps.install)),
        )

    # -- Streaming lifecycle --

    def owns_lease(self, lease: Lease) -> bool:
        state = self._state(lease.tuner.id)
        return state.locked and state.tune_id == lease.tune_id and lease.tune_id not in self._release_tasks

    def touch(self, tune_id: str, nbytes: int) -> None:
        for st in self._states.values():
            if st.tune_id == tune_id:
                st.last_seen = time.time()
                st.bytes_transferred += nbytes
                return

    async def release(self, lease: Lease) -> None:
        # No await before claiming cleanup: concurrent releases share one task.
        # A delayed release from an older stream must never stop a newer tune.
        st = self._state(lease.tuner.id)
        if not st.locked or st.tune_id != lease.tune_id:
            return
        task = self._release_tasks.get(lease.tune_id)
        if task is None:
            task = asyncio.create_task(self._finish_release(lease),
                                       name=f"release-{lease.tune_id}")
            self._release_tasks[lease.tune_id] = task
        # Client disconnect/cancellation cannot interrupt device cleanup.
        await asyncio.shield(task)

    async def _finish_release(self, lease: Lease) -> None:
        try:
            await asyncio.wait_for(self._cleanup_lease(lease), _RELEASE_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001
            st = self._state(lease.tuner.id)
            if st.tune_id == lease.tune_id:
                st.last_error = f"Tuner cleanup failed ({type(exc).__name__}); tuner remains reserved"
            logger.warning("Cleanup failed for tuner %s (%s)", lease.tuner.name, type(exc).__name__)
            # Retain the lease for a later explicit/reaper cleanup retry.
        else:
            if self._state(lease.tuner.id).tune_id == lease.tune_id:
                self._unlock(lease.tuner.id)
            self._leases.pop(lease.tune_id, None)
            logger.info("Released tuner %s (tune %s)", lease.tuner.name, lease.tune_id)
        finally:
            self._release_tasks.pop(lease.tune_id, None)

    async def _cleanup_lease(self, lease: Lease) -> None:
        monitor = lease.monitor_task
        if monitor is not None:
            if not monitor.done():
                monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            except Exception:
                pass
        options = self._options
        task = lease.tune_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # A canceled tune task is expected. Cancellation of this cleanup
                # task (e.g. its deadline), however, must not be swallowed.
                if asyncio.current_task().cancelling():
                    raise
            except Exception:  # noqa: BLE001
                pass
            lease.tune_task = None
        if lease.player_session_key is not None:
            from .fastchannels_probe import stop_player
            await stop_player(lease.backend, lease.player_session_key)
            return
        app_play = lease.tune_configuration
        cmd_be = lease.command_backend or lease.backend
        if app_play is not None and app_play.post_tune_commands:
            chosen_pkg = self._choose_package(lease.tuner.id, lease.channel)
            identifier = lease.channel.url if lease.channel.url is not None else ""
            await run_commands(
                cmd_be,
                app_play.post_tune_commands,
                package=chosen_pkg,
                identifier=identifier,
                skip_am_start=True,
            )
        elif (
            lease.force_stop
            or options.stop_on_release
            or not options.keep_apps_running
            or lease.channel.compatibility_mode
        ):
            await lease.backend.stop()

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
                    "keys_backend": (
                        tuner.keys_control.type if tuner.keys_control else None
                    ),
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
        await self.stream_relay.close()
        await asyncio.gather(*(self.release(lease) for lease in list(self._leases.values())))
        if self._release_tasks:
            await asyncio.gather(*list(self._release_tasks.values()))
        for backend in list(self._backends.values()) + list(self._keys_backends.values()):
            try:
                await backend.close()
            except Exception:  # noqa: BLE001
                pass
        self._backends.clear()
        self._keys_backends.clear()

    async def _reap_once(self) -> None:
        timeout = self._options.stuck_tuner_timeout_seconds
        now = time.time()
        expired = []
        for st in list(self._states.values()):
            if st.locked and st.last_seen and now - st.last_seen > timeout:
                # Synchronous tuning has not returned a lease yet. Never unlock
                # it while the request can still issue commands to the device.
                lease = self._leases.get(st.tune_id)
                if lease is not None:
                    logger.warning("Cleaning up stale tuner %s", st.tuner_id)
                    expired.append(lease)
        await asyncio.gather(*(self.release(lease) for lease in expired))

    async def _reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            await self._reap_once()
