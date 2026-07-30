"""Normalize remote / ADBTuner key names for key_macro and config scripts."""

from __future__ import annotations

from typing import Any, Iterable


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
                tokens.append(_strip_keycode(piece))
    return tokens


def key_requires_dpad(key: str) -> bool:
    """True when the key cannot be sent via Agent Accessibility (BACK/HOME/RECENTS)."""
    k = _strip_keycode(key)
    if k in {"BACK", "HOME", "RECENTS", "APP_SWITCH"}:
        return False
    # Numeric Android keycodes and everything else need a real remote / ADB.
    return True
