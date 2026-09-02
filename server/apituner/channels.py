"""Channel identity helpers and list validation."""

from __future__ import annotations

import uuid
from typing import Literal

from .channel_numbers import channel_number_sort_key, normalize_channel_number
from .models import Channel


class ChannelValidationError(ValueError):
    """Raised when a channel list fails validation."""


def new_channel_id() -> str:
    """Return a URL-safe unique channel id (32-char hex)."""
    return uuid.uuid4().hex


def ensure_channel_ids(channels: list[Channel]) -> bool:
    """Assign ids to channels missing them. Returns True if any were added."""
    changed = False
    for ch in channels:
        if not (ch.id or "").strip():
            ch.id = new_channel_id()
            changed = True
    return changed


def find_duplicate_ids(channels: list[Channel]) -> list[str]:
    """Return channel ids that appear more than once."""
    seen: set[str] = set()
    dups: set[str] = set()
    for ch in channels:
        cid = (ch.id or "").strip()
        if not cid:
            continue
        if cid in seen:
            dups.add(cid)
        seen.add(cid)
    return sorted(dups)


def validate_unique_ids(channels: list[Channel]) -> None:
    """Raise ChannelValidationError if any channel id is duplicated or missing."""
    ensure_channel_ids(channels)
    dups = find_duplicate_ids(channels)
    if dups:
        raise ChannelValidationError(
            f"Duplicate channel ids: {', '.join(dups)}."
        )


ResolveError = Literal["not_found", "ambiguous"]


def resolve_channel(
    channels: list[Channel], token: str
) -> tuple[Channel | None, ResolveError | None]:
    """Resolve a stream/lineup token to a channel.

    Resolution order:
    1. Exact id match
    2. Exact number match (only when exactly one channel has that number)
    3. Dotted ATSC major (e.g. ``5.1`` → ``5``) when exactly one match

    Returns ``(channel, None)`` on success, ``(None, 'ambiguous')`` when the
    token matches multiple channels by number, or ``(None, 'not_found')``.
    """
    token = (token or "").strip()
    if not token:
        return None, "not_found"

    by_id = [ch for ch in channels if ch.id == token]
    if len(by_id) == 1:
        return by_id[0], None
    if len(by_id) > 1:
        return None, "ambiguous"

    by_number = [ch for ch in channels if ch.number == token]
    if len(by_number) == 1:
        return by_number[0], None
    if len(by_number) > 1:
        return None, "ambiguous"

    normalized = normalize_channel_number(token)
    if normalized and normalized != token:
        by_normalized = [ch for ch in channels if ch.number == normalized]
        if len(by_normalized) == 1:
            return by_normalized[0], None
        if len(by_normalized) > 1:
            return None, "ambiguous"

    if "." in token:
        major, _, minor = token.partition(".")
        if major.isdigit() and minor.isdigit():
            by_major = [ch for ch in channels if ch.number == major]
            if len(by_major) == 1:
                return by_major[0], None
            if len(by_major) > 1:
                return None, "ambiguous"

    return None, "not_found"


def sort_channels(channels: list[Channel]) -> list[Channel]:
    """Return channels sorted by guide number (major.minor order)."""
    return sorted(channels, key=lambda ch: channel_number_sort_key(ch.number))


def find_channel(channels: list[Channel], token: str) -> Channel | None:
    """Resolve a token; returns None for not found or ambiguous (HDHR compat)."""
    channel, err = resolve_channel(channels, token)
    if err is not None:
        return None
    return channel
