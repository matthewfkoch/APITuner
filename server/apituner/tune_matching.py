"""MediaSession title matching and tune readiness diagnostic messages."""

from __future__ import annotations

import re
from typing import Optional

from .backends import PlaybackState

_STALE_PLAYBACK_PACKAGES = frozenset(
    {
        "com.att.tv",
        "com.att.tv.openvideo",
        "com.att.ngc",
    }
)
# After two idle relaunches DirecTV can still sit on splash with no
# MediaSession. stream_during_tune already opened HDMI, so keep waiting
# instead of failing at 30s and killing the proxy.
_STALE_SPLASH_GRACE_SECONDS = 45.0
_STALE_SPLASH_GRACE_MIN_TIMEOUT = 20.0
_HOME_BEFORE_DEEPLINK_SECONDS = 0.8
# DirecTV often needs ~10s after a good deeplink before MediaSession flips
# to PLAYING. A 2s idle HOME restarts that launch (MeTV log, 0.1.29).
_STALE_FOREGROUND_IDLE_RELAUNCH_SECONDS = 15.0
# Cold start when the target app is not in front yet. Same-app switches and
# an app that is already open wait the full deeplink_relaunch_seconds instead.
_COLD_START_RESEND_SECONDS = 2.0
# YouTube TV relaunch is CLEAR_TOP|SINGLE_TOP. A second intent at 6s lands
# inside the window where a good first resend becomes PLAYING (5–9s). Wait
# past that, then send one more time if playback is still idle. If the app
# is still in front, that second CLEAR_TOP is ignored (Sports 1, BRAVO).
# HOME first so the next VIEW starts clean, the way a retry after release does.
_NONSTALE_SECOND_RESEND_SECONDS = 12.0

_GENERIC_TITLE_TOKENS = frozenset(
    {
        "live",
        "tv",
        "hd",
        "uhd",
        "4k",
        "video",
        "directv",
        "dtv",
        "att",
        "watch",
        "playing",
        "for",
        "you",
        "home",
        "app",
        "stream",
        "the",
        "and",
        "network",
        "unknown",
        "untitled",
        "loading",
        "buffering",
    }
)
_PLACEHOLDER_TITLES = frozenset(
    {
        "unknown title",
        "unknown",
        "untitled",
        "null",
        "none",
        "n/a",
    }
)


def _title_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) >= 3}


def _title_is_placeholder(title: Optional[str]) -> bool:
    """True for empty / splash MediaSession titles (DirecTV 'Unknown Title')."""
    if not title or not str(title).strip():
        return True
    return str(title).strip().lower() in _PLACEHOLDER_TITLES


def _title_looks_like_channel(title: Optional[str], channel_name: str) -> Optional[bool]:
    """True if title overlaps the channel name, False if it looks like other content, None to ignore."""
    if _title_is_placeholder(title):
        return None
    title_toks = _title_tokens(title)
    name_toks = _title_tokens(channel_name)
    specific = title_toks - _GENERIC_TITLE_TOKENS
    if not specific or not (name_toks - _GENERIC_TITLE_TOKENS):
        return None
    if specific & name_toks:
        return True
    return False


def _titles_equivalent(left: Optional[str], right: Optional[str]) -> bool:
    """True when two MediaSession titles are the same leftover show."""
    if not left or not str(left).strip() or not right or not str(right).strip():
        return False
    if str(left).strip().lower() == str(right).strip().lower():
        return True
    left_toks = _title_tokens(left) - _GENERIC_TITLE_TOKENS
    right_toks = _title_tokens(right) - _GENERIC_TITLE_TOKENS
    if not left_toks or not right_toks:
        return False
    return left_toks <= right_toks or right_toks <= left_toks


def tune_not_ready_message(
    *,
    number: str,
    name: str,
    playback: Optional[PlaybackState],
    title: Optional[str],
    relaunches: int,
    relaunch_error: Optional[str] = None,
    splash_extended: bool = False,
    foreground: Optional[str] = None,
) -> str:
    """One line for the tuner card and diagnostics: what failed, and what to check."""
    state = playback.name if isinstance(playback, PlaybackState) else "UNKNOWN"
    detail = f"playback={state}, relaunches={relaunches}"
    if splash_extended:
        detail += ", splash wait extended"
    if title:
        detail += f", title={title!r}"
    if foreground is not None:
        detail += f", foreground={foreground or 'unknown'!r}"
    head = f"channel {number} ({name}) not ready within timeout ({detail})"
    err = (relaunch_error or "").strip()
    if err:
        if "HTTP verb" in err or "{}POST" in err:
            why = (
                "HOME ran, then the Agent rejected the next deeplink "
                "(malformed HTTP verb). Update the APITuner server."
            )
        else:
            why = (
                "HOME ran, then the deeplink failed, so the TV may be on "
                f"the Android home screen. {err}"
            )
        return f"{head}. {why}"
    if playback == PlaybackState.UNKNOWN:
        return (
            f"{head}. Playback was unknown. On an Agent tuner grant "
            "Notification access. Android TV Remote only sees Cast playback, "
            "which apps like DirecTV do not report."
        )
    if playback == PlaybackState.IDLE:
        return (
            f"{head}. Notification access is working, but the channel never "
            "started playing. Check that the app is installed and overlay "
            "permission is granted."
        )
    return f"{head}."
