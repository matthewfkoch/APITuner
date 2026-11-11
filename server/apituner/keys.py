"""Normalize remote / ADBTuner key names for key_macro and config scripts."""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction
from typing import Any, Iterable, Optional

_SLEEP_TOKEN_RE = re.compile(
    r"^(?:SLEEP|WAIT)[\s_:]+([0-9]+(?:\.[0-9]+)?)$", re.IGNORECASE
)


_WAIT_PREFIX_RE = re.compile(r"^(?:WAIT(?:$|[\s_:])|SLEEP[\s_:])", re.IGNORECASE)
MAX_KEY_MACRO_WAIT_SECONDS = 10


def parse_key_macro_sleep(raw: str) -> Optional[Decimal]:
    """Return the exact decimal duration of a wait token."""
    match = _SLEEP_TOKEN_RE.fullmatch(raw.strip())
    return Decimal(match.group(1)) if match else None


def validate_key_macro_waits(keys: Iterable[str]) -> None:
    total = Fraction()
    for key in keys:
        duration = parse_key_macro_sleep(key)
        if duration is None:
            if _WAIT_PREFIX_RE.match(key):
                raise ValueError(f"Invalid key macro wait {key!r}; use SLEEP or WAIT with seconds")
            continue
        if duration > MAX_KEY_MACRO_WAIT_SECONDS:
            raise ValueError(f"Key macro waits may total at most {MAX_KEY_MACRO_WAIT_SECONDS} seconds")
        total += Fraction(duration)
        if total > MAX_KEY_MACRO_WAIT_SECONDS:
            raise ValueError(f"Key macro waits may total at most {MAX_KEY_MACRO_WAIT_SECONDS} seconds")


def _strip_keycode(raw: str) -> str:
    key = raw.strip()
    if key.upper().startswith("KEYCODE_"):
        key = key[8:]
    return key.upper() if not key.isdigit() else key


def normalize_key_macro(value: Any) -> list[str]:
    """Expand semicolon/comma-delimited key_macro into discrete key names.

    Accepts a string, a list of strings (entries may still contain ``;`` / ``,``),
    or nested iterables. Empty tokens are dropped.
    """
    if value is None:
        return []
    tokens: list[str] = []
    if isinstance(value, str):
        parts: Iterable[Any] = [value]
    elif isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = [str(value)]

    for part in parts:
        if part is None:
            continue
        text = str(part).strip()
        if not text:
            continue
        for piece in text.replace(";", ",").split(","):
            piece = piece.strip()
            if piece:
                key = _strip_keycode(piece)
                duration = parse_key_macro_sleep(key)
                if duration is not None:
                    seconds = format(duration, "f")
                    if "." in seconds:
                        seconds = seconds.rstrip("0").rstrip(".")
                    key = f"SLEEP {seconds}"
                tokens.append(key)
    return tokens


def key_requires_dpad(key: str) -> bool:
    """True when the key cannot be sent via Agent Accessibility (BACK/HOME/RECENTS)."""
    if parse_key_macro_sleep(key) is not None:
        return False
    k = _strip_keycode(key)
    if k in {"BACK", "HOME", "RECENTS", "APP_SWITCH"}:
        return False
    # Numeric Android keycodes and everything else need a real remote / ADB.
    return True
