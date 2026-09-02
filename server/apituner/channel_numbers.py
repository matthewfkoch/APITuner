"""Normalize and sort ATSC-style channel numbers (e.g. 100, 100.1)."""

from __future__ import annotations

from typing import Any


def normalize_channel_number(value: Any) -> str | None:
    """Coerce ADBTuner / M3U channel numbers into canonical string form.

    Whole numbers become integer strings (``3``, ``245``). Subchannels keep a
    dotted minor (``100.1``, ``100.2``). ADBTuner ``sort_order`` values like
    ``3.0`` or ``245.0`` collapse to the major only.
    """
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        text = f"{value:g}"
        return _normalize_dotted_or_int(text)

    text = str(value).strip()
    if not text:
        return None
    return _normalize_dotted_or_int(text)


def _normalize_dotted_or_int(text: str) -> str | None:
    if "." in text:
        major, _, minor = text.partition(".")
        if major.isdigit() and minor.isdigit():
            major_val = int(major)
            minor_val = int(minor)
            if minor_val == 0:
                return str(major_val)
            return f"{major_val}.{minor_val}"
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
        if number.is_integer():
            return str(int(number))
        normalized = f"{number:g}"
        if "." in normalized:
            return _normalize_dotted_or_int(normalized)
        return normalized

    if text.isdigit():
        return text

    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return str(int(number))
    return _normalize_dotted_or_int(f"{number:g}")


def channel_number_sort_key(number: str | int) -> tuple[int, int, str]:
    """Sort key for guide order: major, minor, original string."""
    normalized = normalize_channel_number(number)
    if normalized is None:
        return (0, 0, str(number))
    if "." in normalized:
        major, _, minor = normalized.partition(".")
        return (int(major), int(minor), normalized)
    return (int(normalized), 0, normalized)


def occupied_integer_numbers(numbers: list[str]) -> set[int]:
    """Whole-number dial positions already in use (for FDL auto-numbering)."""
    taken: set[int] = set()
    for raw in numbers:
        normalized = normalize_channel_number(raw)
        if normalized is None or "." in normalized:
            continue
        taken.add(int(normalized))
    return taken


def export_channel_number(number: str) -> int | float:
    """ADBTuner JSON export shape: int majors, float subchannels."""
    if "." in number:
        return float(number)
    return int(number)
