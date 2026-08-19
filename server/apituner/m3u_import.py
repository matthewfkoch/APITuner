"""Parse FruitDeepLinks / ADBTuner M3U playlists into APITuner channel dicts."""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .deeplink_catalog import (
    infer_provider,
    parse_lane_url,
    parse_virtual_stream_url,
    resolve_packages,
)

_ATTR = re.compile(
    r"""([A-Za-z0-9_-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+))"""
)

SOURCE_FRUITDEEPLINKS = "fruitdeeplinks"


def parse_extinf_attrs(line: str) -> tuple[dict[str, str], str]:
    """Return (attrs, display_name) from an #EXTINF line."""
    body = line.strip()
    if body.upper().startswith("#EXTINF:"):
        body = body[8:]
    comma = _split_extinf(body)
    if comma is None:
        return {}, body.strip()
    meta, name = body[:comma], body[comma + 1 :]
    attrs: dict[str, str] = {}
    for match in _ATTR.finditer(meta):
        key = match.group(1).lower()
        value = match.group(2) or match.group(3) or match.group(4) or ""
        attrs[key] = value
    return attrs, name.strip()


def _split_extinf(body: str) -> Optional[int]:
    """Index of the comma that separates attributes from the display name."""
    in_quote: Optional[str] = None
    for i, ch in enumerate(body):
        if in_quote:
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ('"', "'"):
            in_quote = ch
            continue
        if ch == ",":
            return i
    return None


def whatson_resolver_url(base: str, lane: int) -> str:
    root = (base or "").rstrip("/")
    return normalize_resolver_url(f"{root}/whatson/{lane}?include=deeplink")


def rewrite_playlist_url(url: str) -> str:
    """Turn FDL HLS /lane/N/stream.m3u8 entries into /whatson/N resolvers."""
    raw = (url or "").strip()
    lane = parse_virtual_stream_url(raw)
    if lane is not None:
        parsed = urlparse(raw)
        base = f"{parsed.scheme}://{parsed.netloc}"
        return whatson_resolver_url(base, lane)
    return normalize_resolver_url(raw)


def normalize_resolver_url(url: str) -> str:
    """Keep lane resolvers; prefer JSON + deeplink_url key for empty-lane errors."""
    raw = (url or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    path = (parsed.path or "").lower()
    is_lane = (
        "/lanes/" in path
        or "/whatson/" in path
        or "/api/adb/" in path
    )
    if not is_lane:
        return raw
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if "dynamic_url_json_key" not in qs:
        qs["dynamic_url_json_key"] = ["deeplink_url"]
    if "/whatson/" in path and "include" not in qs and "deeplink" not in qs:
        qs["include"] = ["deeplink"]
    fmt = (qs.get("format") or [""])[0].lower()
    if fmt not in ("json", "text", "txt"):
        qs["format"] = ["json"]
    elif fmt in ("text", "txt"):
        qs["format"] = ["json"]
    flat: list[tuple[str, str]] = []
    for key, values in qs.items():
        for value in values:
            flat.append((key, value))
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(flat), parsed.fragment)
    )


def _int_attr(attrs: dict[str, str], *keys: str) -> Optional[int]:
    for key in keys:
        raw = attrs.get(key)
        if raw is None or raw == "":
            continue
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            continue
    return None


def channels_from_m3u(
    text: str,
    *,
    profile: Optional[str] = None,
    start_number: int = 9000,
    source: Optional[str] = SOURCE_FRUITDEEPLINKS,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Parse M3U into channel dicts. Returns (channels, skipped)."""
    lines = (text or "").splitlines()
    pending_attrs: dict[str, str] = {}
    pending_name = ""
    next_number = int(start_number)
    used: set[int] = set()
    channels: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    def flush(url: str) -> None:
        nonlocal next_number
        url = rewrite_playlist_url(url.strip())
        if not url or url.startswith("#"):
            return
        attrs = dict(pending_attrs)
        name = pending_name or attrs.get("tvg-name") or attrs.get("tvg-id") or url
        number = _int_attr(attrs, "tvg-chno", "channel-number", "tvg-channel")
        if number is None:
            while next_number in used:
                next_number += 1
            number = next_number
            next_number += 1
        used.add(number)
        pkg_override = (
            attrs.get("package-name")
            or attrs.get("package_name")
            or attrs.get("tvg-package")
        )
        alt_override = (
            attrs.get("alternate-package-name")
            or attrs.get("alternate_package_name")
        )
        group = attrs.get("group-title")
        lane = parse_lane_url(url)
        provider = (attrs.get("provider") or "").strip() or None
        if lane:
            provider = provider or lane[0]
        provider = provider or infer_provider(url) or (group.strip() if group else None)
        code, package, alternate = resolve_packages(
            url=url,
            provider_code=provider if provider and " " not in str(provider) else (lane[0] if lane else None),
            profile=profile,
            package_name=pkg_override,
            alternate_package_name=alt_override,
        )
        if not package:
            from .deeplink_catalog import packages_for

            fallback = packages_for("apple_other", profile=profile)
            if fallback and parse_virtual_stream_url(url.strip()) is None and "/whatson/" in url:
                package, alternate = fallback
                code = code or "apple_other"
            else:
                skipped.append(
                    {
                        "name": name,
                        "reason": f"no Android package mapping for provider {code or provider or 'unknown'!r}",
                    }
                )
                return
        launch_url = url if "/whatson/" in url else normalize_resolver_url(url)
        channels.append(
            {
                "number": number,
                "name": name,
                "provider_name": code or provider,
                "package_name": package,
                "alternate_package_name": alternate,
                "url": launch_url,
                "action": "android.intent.action.VIEW",
                "source": source,
            }
        )

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF:"):
            pending_attrs, pending_name = parse_extinf_attrs(line)
            continue
        if line.startswith("#"):
            continue
        flush(line)
        pending_attrs, pending_name = {}, ""

    return channels, skipped
