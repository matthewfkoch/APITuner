"""Validate channel lists before persisting."""

from __future__ import annotations

from .models import Channel


class ChannelValidationError(ValueError):
    """Raised when a channel list has invalid or duplicate numbers."""


def find_duplicate_numbers(channels: list[Channel]) -> list[int]:
    """Return channel numbers that appear more than once."""
    seen: set[int] = set()
    dups: set[int] = set()
    for ch in channels:
        if ch.number in seen:
            dups.add(ch.number)
        seen.add(ch.number)
    return sorted(dups)


def validate_channel_numbers(channels: list[Channel]) -> None:
    """Raise ChannelValidationError if any channel number is duplicated."""
    dups = find_duplicate_numbers(channels)
    if not dups:
        return
    details: list[str] = []
    for num in dups:
        names = [ch.name for ch in channels if ch.number == num]
        joined = " / ".join(names) if names else "?"
        details.append(f"{num} ({joined})")
    raise ChannelValidationError(
        f"Duplicate channel numbers: {', '.join(details)}. "
        "Give each channel a unique number before importing."
    )
