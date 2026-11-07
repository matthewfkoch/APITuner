"""Normalize remote / ADBTuner key names for key_macro and config scripts."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

_SLEEP_TOKEN_RE = re.compile(
    r"^(?:SLEEP|WAIT)[\s_:]+([0-9]+(?:\.[0-9]+)?)$", re.IGNORECASE
)


def parse_key_macro_sleep(raw: str) -> Optional[float]:
    """Return sleep duration in seconds when ``raw`` is a SLEEP/WAIT token, else None."""
    if not raw:
        return None
    m = _SLEEP_TOKEN_RE.match(raw.strip())
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    return max(0.0, val)


def _strip_keycode(raw: str) -> str:
    sleep_sec = parse_key_macro_sleep(raw)
    if sleep_sec is not None:
        return f"SLEEP {sleep_sec:g}"
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
                tokens.append(_strip_keycode(piece))
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
