"""Playback & foreground readiness state machine for channel tuning."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .backends import Capabilities, ControlBackend, PlaybackState
from .models import Channel, GlobalOptions
from .packages import package_candidates
from .tune_matching import (
    _COLD_START_RESEND_SECONDS,
    _HOME_BEFORE_DEEPLINK_SECONDS,
    _NONSTALE_SECOND_RESEND_SECONDS,
    _STALE_FOREGROUND_IDLE_RELAUNCH_SECONDS,
    _STALE_PLAYBACK_PACKAGES,
    _STALE_SPLASH_GRACE_MIN_TIMEOUT,
    _STALE_SPLASH_GRACE_SECONDS,
    _title_is_placeholder,
    _title_looks_like_channel,
    _titles_equivalent,
    tune_not_ready_message,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadinessTimings:
    stale_splash_grace_seconds: float = _STALE_SPLASH_GRACE_SECONDS
    stale_splash_grace_min_timeout: float = _STALE_SPLASH_GRACE_MIN_TIMEOUT
    home_before_deeplink_seconds: float = _HOME_BEFORE_DEEPLINK_SECONDS
    stale_foreground_idle_relaunch_seconds: float = _STALE_FOREGROUND_IDLE_RELAUNCH_SECONDS
    cold_start_resend_seconds: float = _COLD_START_RESEND_SECONDS
    nonstale_second_resend_seconds: float = _NONSTALE_SECOND_RESEND_SECONDS


async def home_off_splash(
    backend: ControlBackend,
    *,
    delay_seconds: float = _HOME_BEFORE_DEEPLINK_SECONDS,
) -> None:
    """Best-effort HOME so a stuck splash does not swallow the next VIEW intent."""
    try:
        await backend.stop()
    except Exception as exc:  # noqa: BLE001
        logger.debug("HOME before deeplink failed: %s", exc)
        return
    await asyncio.sleep(delay_seconds)


class _TuneReadinessWaiter:
    """Poll playback/foreground signals until the tuned channel is ready or times out."""

    def __init__(
        self,
        *,
        backend: ControlBackend,
        caps: Capabilities,
        channel: Channel,
        chosen_pkg: str,
        options: GlobalOptions,
        deadline: float,
        timings: ReadinessTimings,
        prior_app: Optional[str] = None,
        launch_at: Optional[float] = None,
        relaunch: Optional[Callable[[], Awaitable[None]]] = None,
        leftover_title: Optional[str] = None,
        failure: Optional[list[str]] = None,
        foreground_backend: Optional[ControlBackend] = None,
        prefer_foreground_backend: bool = False,
        home_fn: Optional[Callable[[ControlBackend], Awaitable[None]]] = None,
    ) -> None:
        self.loop = asyncio.get_event_loop()
        self.backend = backend
        self.caps = caps
        self.channel = channel
        self.options = options
        self.deadline = deadline
        self.timings = timings
        self.relaunch = relaunch
        self.failure = failure
        self.foreground_backend = foreground_backend
        self.prefer_foreground_backend = prefer_foreground_backend
        self._home_fn = home_fn

        self.targets = {chosen_pkg, channel.package_name}
        if channel.alternate_package_name:
            self.targets.add(channel.alternate_package_name)
        self.targets.update(
            package_candidates(channel.package_name, channel.alternate_package_name)
        )

        self.launch_at = launch_at or self.loop.time()
        self.same_app_switch = (
            prior_app is not None
            and prior_app in self.targets
            and bool(channel.url)
        )
        self.same_app_ready_delay = 2.0
        self.use_playback = options.wait_for_playback and caps.playback_state
        self.playback_unknown_since: Optional[float] = None
        self.playback_idle_since: Optional[float] = None
        self.non_playing_since: Optional[float] = None

        self.relaunch_sec = float(options.deeplink_relaunch_seconds or 0.0)
        self.can_relaunch = relaunch is not None and self.relaunch_sec > 0
        self.relaunch_count = 0
        self.stale_pkg = any(p in _STALE_PLAYBACK_PACKAGES for p in self.targets)
        # DirecTV and other deeplink apps both get two resends. The second
        # non-DirecTV resend waits nonstale_second_resend_seconds so it does
        # not CLEAR_TOP a launch that is about to play. If YouTube TV is still
        # in front at that second send, HOME before the deeplink.
        self.max_relaunches = 2
        self.splash_extended = False
        self.last_ps: Optional[PlaybackState] = None
        self.last_title: Optional[str] = None
        self.first_relaunch_sec = (
            min(timings.cold_start_resend_seconds, self.relaunch_sec)
            if not self.same_app_switch
            else self.relaunch_sec
        )
        self.relaunched = False
        self.last_relaunch_ok = False
        self.last_relaunch_error: Optional[str] = None
        self.saw_playing = False
        self.seen_non_playing = False
        self.stale_playing = bool(self.same_app_switch and self.stale_pkg)
        self.leftover_title = (
            str(leftover_title).strip() or None if leftover_title is not None else None
        )
        self.had_leftover = bool(self.leftover_title)

    async def _read_foreground(self, source: ControlBackend) -> Optional[str]:
        try:
            app = await source.current_app()
        except Exception:  # noqa: BLE001
            return None
        text = str(app).strip() if app else ""
        return text or None

    async def _foreground_package(self) -> Optional[str]:
        extra = self.foreground_backend if self.foreground_backend is not self.backend else None
        if self.prefer_foreground_backend and extra is not None:
            app = await self._read_foreground(extra)
            if app:
                return app
            extra = None
        if self.caps.current_app:
            app = await self._read_foreground(self.backend)
            if app:
                return app
        if extra is not None:
            return await self._read_foreground(extra)
        return None

    async def _foreground_in_targets(self) -> bool:
        app = await self._foreground_package()
        return bool(app and app in self.targets)

    async def _idle_resend_wait(self) -> float:
        """Seconds from the last intent before the next non-DirecTV idle resend."""
        if self.relaunch_count == 0:
            # App already open: a 2s CLEAR_TOP can reset a launch that is
            # about to produce a media session. Wait the full interval.
            if await self._foreground_in_targets():
                return self.relaunch_sec
            return self.first_relaunch_sec
        return self.timings.nonstale_second_resend_seconds

    async def _snapshot(self) -> tuple[PlaybackState, Optional[str], Optional[str]]:
        snap_fn = getattr(self.backend, "playback_snapshot", None)
        if callable(snap_fn):
            try:
                out = await snap_fn()
                if isinstance(out, tuple) and len(out) == 3:
                    return out[0], out[1], out[2]
            except Exception:  # noqa: BLE001
                pass
        return await self.backend.playback_state(), None, None

    async def _not_ready(self) -> bool:
        looked_up = self.caps.current_app or (
            self.foreground_backend is not None and self.foreground_backend is not self.backend
        )
        foreground: Optional[str] = None
        if looked_up:
            foreground = await self._foreground_package() or ""
        message = tune_not_ready_message(
            number=str(self.channel.number),
            name=self.channel.name,
            playback=self.last_ps,
            title=self.last_title,
            relaunches=self.relaunch_count,
            relaunch_error=self.last_relaunch_error,
            splash_extended=self.splash_extended,
            foreground=foreground,
        )
        logger.warning("%s", message)
        if self.failure is not None:
            self.failure[:] = [message]
        return False

    async def _maybe_relaunch(self, reason: str) -> None:
        if not self.can_relaunch or self.relaunch_count >= self.max_relaunches:
            return
        # Splash / "Unknown Title" is still loading — use the full interval
        # so a 2s cold-start retry does not kill a working first tune.
        if reason == "generic_title":
            wait = self.relaunch_sec
        elif reason == "idle" and not self.stale_pkg:
            wait = await self._idle_resend_wait()
        elif self.relaunch_count == 0:
            wait = self.first_relaunch_sec
        else:
            wait = self.relaunch_sec
        if (
            reason == "idle"
            and self.stale_pkg
            and self.relaunch_count == 0
            and not self.same_app_switch
            and self.relaunch_sec >= 2.0
            and await self._foreground_in_targets()
        ):
            # App is open and still starting. HOME here restarts it.
            wait = max(wait, self.timings.stale_foreground_idle_relaunch_seconds)
        if self.loop.time() - self.launch_at < wait:
            return
        # A dropped deeplink stays on the launcher. Re-send it. DirecTV
        # that is already open waits longer above so HOME does not restart it.
        if (
            reason != "idle"
            and not self.stale_pkg
            and not await self._foreground_in_targets()
        ):
            return
        foreground_pkg = await self._foreground_package()
        # Another CLEAR_TOP into an app that ignored the first resend stays
        # IDLE. HOME, then the relaunch is a fresh VIEW. A second resend
        # while the app never came forward (launcher) stays a plain intent.
        if (
            reason == "idle"
            and not self.stale_pkg
            and self.relaunch_count >= 1
            and foreground_pkg in self.targets
        ):
            logger.info(
                "HOME before deeplink resend for channel %s (%s) — %s still idle in front",
                self.channel.number,
                self.channel.name,
                foreground_pkg,
            )
            if self._home_fn is not None:
                await self._home_fn(self.backend)
            else:
                await home_off_splash(
                    self.backend, delay_seconds=self.timings.home_before_deeplink_seconds
                )
        try:
            await self.relaunch()  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            self.last_relaunch_error = str(exc)
            logger.warning(
                "Deeplink relaunch failed for %s (%s) after HOME: %s. "
                "The TV may be on the Android home screen.",
                self.channel.number,
                self.channel.name,
                exc,
            )
            self.relaunch_count += 1
            self.relaunched = True
            self.last_relaunch_ok = False
            self.launch_at = self.loop.time()
            return
        self.relaunch_count += 1
        self.relaunched = True
        self.last_relaunch_ok = True
        self.non_playing_since = self.loop.time()
        self.launch_at = self.loop.time()
        self.seen_non_playing = False
        logger.info(
            "Re-sent deeplink for channel %s (%s) (%s, %s/%s, foreground=%s)",
            self.channel.number,
            self.channel.name,
            reason,
            self.relaunch_count,
            self.max_relaunches,
            foreground_pkg or "unknown",
        )

    async def _evaluate_playing(
        self, sess_pkg: Optional[str], sess_title: Optional[str]
    ) -> bool:
        title_match = _title_looks_like_channel(sess_title, self.channel.name)
        accept = False
        # Remember splash / continue-watching so a later show
        # change counts (Unknown Title → Funny You Should Ask).
        if (
            self.leftover_title is None
            and sess_title
            and title_match is not True
            and (self.stale_pkg or self.relaunch_count > 0)
        ):
            self.leftover_title = str(sess_title).strip() or None
        still_leftover = _titles_equivalent(sess_title, self.leftover_title)
        title_changed = bool(
            self.leftover_title
            and sess_title
            and not still_leftover
            and not _title_is_placeholder(sess_title)
        )
        if title_match is True or title_changed:
            accept = True
        elif title_match is False:
            if not self.stale_pkg:
                if self.same_app_switch and self.had_leftover and still_leftover:
                    logger.info(
                        "Ignoring playback title %r while tuning %s",
                        sess_title,
                        self.channel.name,
                    )
                    await self._maybe_relaunch("title_mismatch")
                else:
                    accept = True
            elif (
                self.stale_pkg
                and not self.had_leftover
                and not self.same_app_switch
                and self.relaunch_count == 0
                and sess_title
                and not _title_is_placeholder(sess_title)
            ):
                accept = True
            elif (
                self.stale_pkg
                and self.relaunch_count >= self.max_relaunches
                and not self.had_leftover
            ):
                accept = True
            else:
                logger.info(
                    "Ignoring playback title %r while tuning %s",
                    sess_title,
                    self.channel.name,
                )
                await self._maybe_relaunch("title_mismatch")
        elif self.stale_playing and not self.seen_non_playing:
            if self.relaunch_count < self.max_relaunches:
                await self._maybe_relaunch("stale_playing")
            elif self.loop.time() - self.launch_at >= 1.5:
                accept = True
        elif self.stale_pkg:
            await self._maybe_relaunch("generic_title")
        else:
            accept = True

        if not accept:
            return False

        self.saw_playing = True
        settle = max(0.0, float(self.options.ready_settle_seconds))
        if settle:
            await asyncio.sleep(min(settle, max(0.0, self.deadline - self.loop.time())))
        if sess_title:
            logger.info(
                "Playback ready for %s title=%r package=%s",
                self.channel.name,
                sess_title,
                sess_pkg,
            )
        return True

    async def _handle_non_playing(self, ps: PlaybackState) -> None:
        self.seen_non_playing = True
        if self.can_relaunch:
            if self.non_playing_since is None:
                self.non_playing_since = self.loop.time()
            idle_elapsed = self.loop.time() - self.non_playing_since
            if not self.stale_pkg:
                wait = await self._idle_resend_wait()
            else:
                wait = self.first_relaunch_sec if self.relaunch_count == 0 else self.relaunch_sec
            if self.relaunch_count < self.max_relaunches and idle_elapsed >= wait:
                await self._maybe_relaunch("idle")
            if (
                self.stale_pkg
                and self.last_relaunch_ok
                and self.relaunch_count >= self.max_relaunches
                and not self.splash_extended
                and float(self.options.tune_timeout_seconds or 0)
                >= self.timings.stale_splash_grace_min_timeout
            ):
                extra = self.timings.stale_splash_grace_seconds
                self.deadline = max(self.deadline, self.loop.time() + extra)
                self.splash_extended = True
                logger.info(
                    "Extending splash wait for %s by %.0fs after %s idle relaunches",
                    self.channel.name,
                    extra,
                    self.relaunch_count,
                )
        elif ps == PlaybackState.UNKNOWN:
            self.non_playing_since = None
            if self.playback_unknown_since is None:
                self.playback_unknown_since = self.loop.time()
            elif self.loop.time() - self.playback_unknown_since > 3.0:
                self.use_playback = False
        else:
            self.playback_unknown_since = None
            if self.playback_idle_since is None:
                self.playback_idle_since = self.loop.time()
            elif self.loop.time() - self.playback_idle_since > 3.0:
                self.use_playback = False

    async def run(self) -> bool:
        # No readiness signal at all: fixed short delay then accept.
        if not self.use_playback and not self.caps.current_app:
            await asyncio.sleep(min(3.0, max(0.0, self.deadline - self.loop.time())))
            return True

        if self.use_playback and self.leftover_title is None:
            try:
                _ps0, _pkg0, initial_title = await self._snapshot()
                if initial_title is not None:
                    self.leftover_title = str(initial_title).strip() or None
                if (
                    _ps0 == PlaybackState.PLAYING
                    and self.leftover_title
                    and self.same_app_switch
                ):
                    self.had_leftover = True
            except Exception:  # noqa: BLE001
                self.leftover_title = None

        while self.loop.time() < self.deadline:
            if self.use_playback:
                ps, sess_pkg, sess_title = await self._snapshot()
                self.last_ps, self.last_title = ps, sess_title
                if ps == PlaybackState.PLAYING:
                    if await self._evaluate_playing(sess_pkg, sess_title):
                        return True
                if ps in (PlaybackState.UNKNOWN, PlaybackState.IDLE):
                    await self._handle_non_playing(ps)
                else:
                    self.playback_unknown_since = None
                    self.playback_idle_since = None

            if not self.use_playback:
                if (
                    self.same_app_switch
                    and not self.stale_playing
                    and self.loop.time() - self.launch_at >= self.same_app_ready_delay
                ):
                    return True
                if not self.stale_playing:
                    app = await self._foreground_package()
                    if app and app in self.targets:
                        return True

            poll = 0.75
            if self.can_relaunch and self.relaunch_count < self.max_relaunches:
                if not self.stale_pkg:
                    wait = await self._idle_resend_wait()
                else:
                    wait = (
                        self.first_relaunch_sec
                        if self.relaunch_count == 0
                        else self.relaunch_sec
                    )
                due_in = wait - (self.loop.time() - self.launch_at)
                if due_in > 0:
                    poll = min(poll, max(0.05, due_in))
            elif self.splash_extended:
                poll = min(poll, 0.4)
            remaining = self.deadline - self.loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll, remaining))

        if self.stale_pkg and not self.saw_playing:
            return await self._not_ready()
        if self.same_app_switch:
            return True
        if self.relaunched and not self.saw_playing:
            return await self._not_ready()
        app = await self._foreground_package()
        if app and app in self.targets:
            return True
        return await self._not_ready()


async def wait_for_tune_ready(
    *,
    backend: ControlBackend,
    caps: Capabilities,
    channel: Channel,
    chosen_pkg: str,
    options: GlobalOptions,
    deadline: float,
    timings: Optional[ReadinessTimings] = None,
    prior_app: Optional[str] = None,
    launch_at: Optional[float] = None,
    relaunch: Optional[Callable[[], Awaitable[None]]] = None,
    leftover_title: Optional[str] = None,
    failure: Optional[list[str]] = None,
    foreground_backend: Optional[ControlBackend] = None,
    prefer_foreground_backend: bool = False,
    home_fn: Optional[Callable[[ControlBackend], Awaitable[None]]] = None,
) -> bool:
    waiter = _TuneReadinessWaiter(
        backend=backend,
        caps=caps,
        channel=channel,
        chosen_pkg=chosen_pkg,
        options=options,
        deadline=deadline,
        timings=timings or ReadinessTimings(),
        prior_app=prior_app,
        launch_at=launch_at,
        relaunch=relaunch,
        leftover_title=leftover_title,
        failure=failure,
        foreground_backend=foreground_backend,
        prefer_foreground_backend=prefer_foreground_backend,
        home_fn=home_fn,
    )
    return await waiter.run()
