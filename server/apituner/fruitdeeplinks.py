"""Sync FruitDeepLinks ADB lanes or virtual lanes into APITuner channels."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

from .channel_numbers import occupied_integer_numbers
from .channels import ChannelValidationError
from .config import ConfigStore
from .deeplink_catalog import (
    code_from_display_name,
    packages_for,
    parse_lane_url,
    parse_whatson_url,
)
from .m3u_import import (
    SOURCE_FRUITDEEPLINKS,
    channels_from_m3u,
    normalize_resolver_url,
    whatson_resolver_url,
)
from .models import Channel

logger = logging.getLogger(__name__)

FDL_SOURCE = SOURCE_FRUITDEEPLINKS


def fdl_deterministic_channel_id(code: str, lane: int | str) -> str:
    """Generate a stable 32-char hex channel ID for a FruitDeepLinks lane."""
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"fdl:{str(code).lower()}:{str(lane).lower()}").hex


class FruitDeepLinksError(Exception):
    """Raised when FruitDeepLinks cannot be reached or parsed."""


def _base_url(options) -> str:
    url = (options.fruitdeeplinks_url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


def _next_free(number: int, taken: set[int]) -> tuple[int, int]:
    while number in taken:
        number += 1
    chosen = number
    taken.add(chosen)
    return chosen, number + 1


def lane_resolver_url(base: str, provider: str, lane: int) -> str:
    path = f"/api/adb/lanes/{provider}/{lane}/deeplink"
    return normalize_resolver_url(urljoin(base.rstrip("/") + "/", path.lstrip("/")))


def channels_from_adb_lanes(
    providers: list[dict[str, Any]],
    *,
    base_url: str,
    profile: str,
    start_number: int,
    occupied: Optional[set[int]] = None,
) -> tuple[list[Channel], list[dict[str, str]]]:
    """Build Channel models from GET /api/adb/lanes."""
    taken = set(occupied or ())
    number = int(start_number)
    channels: list[Channel] = []
    skipped: list[dict[str, str]] = []

    for item in sorted(providers, key=lambda p: str(p.get("provider_code") or "")):
        code = str(item.get("provider_code") or "").strip().lower()
        if not code:
            continue
        enabled = item.get("adb_enabled")
        if enabled in (0, "0", False, "false", "False"):
            continue
        try:
            count = int(item.get("adb_lane_count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            continue
        mapped = packages_for(code, profile=profile)
        if not mapped:
            skipped.append(
                {
                    "name": code,
                    "reason": f"no Android package mapping for provider {code!r}",
                }
            )
            continue
        package, alternate = mapped
        for lane in range(1, count + 1):
            ch_number, number = _next_free(number, taken)
            channels.append(
                Channel(
                    id=fdl_deterministic_channel_id(code, lane),
                    number=ch_number,
                    name=f"{code} {lane}",
                    provider_name=code,
                    package_name=package,
                    alternate_package_name=alternate,
                    url=lane_resolver_url(base_url, code, lane),
                    action="android.intent.action.VIEW",
                    source=FDL_SOURCE,
                )
            )
    return channels, skipped


def channels_from_virtual_lanes(
    lanes: list[dict[str, Any]],
    *,
    base_url: str,
    profile: str,
    start_number: int,
    occupied: Optional[set[int]] = None,
) -> tuple[list[Channel], list[dict[str, str]]]:
    """Build mixed-provider virtual lanes (GET /api/lanes + /whatson/{id})."""
    taken = set(occupied or ())
    number = int(start_number)
    channels: list[Channel] = []
    skipped: list[dict[str, str]] = []
    placeholder = packages_for("apple_other", profile=profile) or (
        "com.apple.atve.androidtv.appletv",
        None,
    )

    for item in lanes:
        if not isinstance(item, dict):
            continue
        try:
            lane_id = int(item.get("lane_id") or item.get("lane") or 0)
        except (TypeError, ValueError):
            lane_id = 0
        if lane_id <= 0:
            continue
        current = item.get("current") if isinstance(item.get("current"), dict) else {}
        display = (current or {}).get("channel_name") or ""
        code = code_from_display_name(display)
        mapped = packages_for(code, profile=profile) if code else None
        package, alternate = mapped or placeholder
        ch_number, number = _next_free(number, taken)
        name = f"Fruit Lane {lane_id}"
        if display:
            name = f"{name} ({display})"
        channels.append(
            Channel(
                id=fdl_deterministic_channel_id("whatson", lane_id),
                number=ch_number,
                name=name,
                provider_name=code or (display or None),
                package_name=package,
                alternate_package_name=alternate,
                url=whatson_resolver_url(base_url, lane_id),
                action="android.intent.action.VIEW",
                source=FDL_SOURCE,
            )
        )
    if not channels:
        skipped.append(
            {
                "name": "lanes",
                "reason": "FruitDeepLinks /api/lanes returned no lane_id rows",
            }
        )
    return channels, skipped


def _parse_adb_providers(data: Any) -> Optional[list[dict[str, Any]]]:
    providers = data.get("providers") if isinstance(data, dict) else data
    if not isinstance(providers, list):
        return None
    rows = [p for p in providers if isinstance(p, dict)]
    enabled = [
        p
        for p in rows
        if p.get("adb_enabled") not in (0, "0", False, "false", "False")
        and int(p.get("adb_lane_count") or 0) > 0
    ]
    return enabled or None


async def _get(
    client: httpx.AsyncClient, url: str
) -> Optional[httpx.Response]:
    try:
        resp = await client.get(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FruitDeepLinks fetch %s failed: %s", url, exc)
        return None
    if resp.status_code >= 400:
        logger.info("FruitDeepLinks %s -> HTTP %s", url, resp.status_code)
        return None
    return resp


def _channel_lane_key(channel: Channel) -> Optional[tuple[str, int]]:
    url = (channel.url or "").strip()
    if not url:
        return None
    lane = parse_lane_url(url)
    if lane is not None:
        return lane
    whatson = parse_whatson_url(url)
    if whatson is not None:
        return ("whatson", whatson)
    return None


def apply_fdl_channels(store: ConfigStore, incoming: list[Channel]) -> int:
    """Replace existing fruitdeeplinks channels while preserving user customizations."""
    existing_fdl = [
        c for c in store.config.channels if (c.source or "").strip() == FDL_SOURCE
    ]
    by_id: dict[str, Channel] = {c.id.strip(): c for c in existing_fdl if c.id}
    by_url: dict[str, Channel] = {c.url.strip(): c for c in existing_fdl if c.url}
    by_lane_key: dict[tuple[str, int], Channel] = {}
    for c in existing_fdl:
        lane_key = _channel_lane_key(c)
        if lane_key is not None:
            by_lane_key[lane_key] = c
    by_name: dict[str, Channel] = {
        c.name.strip().lower(): c for c in existing_fdl if c.name
    }
    by_provider_lane: dict[tuple[str, str], Channel] = {}
    for c in existing_fdl:
        if c.provider_name and c.name:
            by_provider_lane[(c.provider_name.strip().lower(), c.name.strip().lower())] = c

    merged: list[Channel] = []
    for inc in incoming:
        match: Optional[Channel] = None
        inc_lane_key = _channel_lane_key(inc)
        if inc.id and inc.id.strip() in by_id:
            match = by_id[inc.id.strip()]
        elif inc.url and inc.url.strip() in by_url:
            match = by_url[inc.url.strip()]
        elif inc_lane_key is not None and inc_lane_key in by_lane_key:
            match = by_lane_key[inc_lane_key]
        elif (
            inc.provider_name
            and inc.name
            and (inc.provider_name.strip().lower(), inc.name.strip().lower())
            in by_provider_lane
        ):
            match = by_provider_lane[
                (inc.provider_name.strip().lower(), inc.name.strip().lower())
            ]
        elif inc.name and inc.name.strip().lower() in by_name:
            match = by_name[inc.name.strip().lower()]

        if match is not None:
            data = inc.model_dump()
            if match.id:
                data["id"] = match.id
            if match.number is not None:
                data["number"] = match.number
            if match.name and match.name.strip():
                data["name"] = match.name
            if match.key_macro is not None:
                data["key_macro"] = match.key_macro
            if match.configuration_uuid is not None:
                data["configuration_uuid"] = match.configuration_uuid
            if match.tvc_guide_stationid is not None:
                data["tvc_guide_stationid"] = match.tvc_guide_stationid
            if match.tvg_id is not None:
                data["tvg_id"] = match.tvg_id
            if match.compatibility_mode:
                data["compatibility_mode"] = match.compatibility_mode
            merged.append(Channel.model_validate(data))
        else:
            merged.append(inc)

    return store.replace_source_channels(FDL_SOURCE, merged)


async def sync_fruitdeeplinks(store: ConfigStore) -> dict[str, Any]:
    """Sync FDL ADB provider lanes, or virtual /whatson lanes if ADB is not exported."""
    options = store.config.options
    base = _base_url(options)
    if not base:
        raise FruitDeepLinksError("fruitdeeplinks_url is not set")
    occupied = occupied_integer_numbers(
        [c.number for c in store.config.channels if c.source != FDL_SOURCE]
    )
    profile = options.fruitdeeplinks_profile
    start = int(options.fruitdeeplinks_start_number or 9000)
    timeout = float(options.request_timeout or 10.0)
    incoming: list[Channel] = []
    skipped: list[dict[str, str]] = []
    mode = ""

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        adb = await _get(client, base + "/api/provider_lanes")
        if adb is None:
            adb = await _get(client, base + "/api/adb/lanes")
        if adb is not None:
            try:
                providers = _parse_adb_providers(adb.json())
            except Exception:  # noqa: BLE001
                providers = None
            if providers:
                incoming, skipped = channels_from_adb_lanes(
                    providers,
                    base_url=base,
                    profile=profile,
                    start_number=start,
                    occupied=occupied,
                )
                mode = "adb"

        if not incoming:
            m3u_adb = await _get(client, base + "/m3u/adb")
            if m3u_adb is not None and (m3u_adb.text or "").lstrip().upper().startswith(
                "#EXTM3U"
            ):
                dicts, skipped = channels_from_m3u(
                    m3u_adb.text, profile=profile, start_number=start
                )
                taken = set(occupied or ())
                curr = start
                incoming = []
                for row in dicts:
                    ch_number, curr = _next_free(curr, taken)
                    row["number"] = str(ch_number)
                    incoming.append(Channel.model_validate(row))
                mode = "m3u_adb"

        if not incoming:
            lanes_resp = await _get(client, base + "/api/lanes")
            lanes: Optional[list] = None
            if lanes_resp is not None:
                try:
                    data = lanes_resp.json()
                    lanes = data if isinstance(data, list) else data.get("lanes")
                except Exception:  # noqa: BLE001
                    lanes = None
            if isinstance(lanes, list) and lanes:
                incoming, skipped = channels_from_virtual_lanes(
                    lanes,
                    base_url=base,
                    profile=profile,
                    start_number=start,
                    occupied=occupied,
                )
                mode = "whatson"

        if not incoming:
            m3u_lanes = await _get(client, base + "/m3u/lanes")
            if m3u_lanes is not None and (m3u_lanes.text or "").lstrip().upper().startswith(
                "#EXTM3U"
            ):
                dicts, skipped = channels_from_m3u(
                    m3u_lanes.text, profile=profile, start_number=start
                )
                incoming = [Channel.model_validate(row) for row in dicts]
                mode = "m3u_lanes"

    if not incoming:
        raise FruitDeepLinksError(
            f"{base} has no ADB provider lanes API (/api/provider_lanes or /api/adb/lanes) "
            "and no usable /m3u/adb, /api/lanes, or /m3u/lanes playlist."
        )

    try:
        count = apply_fdl_channels(store, incoming)
    except ChannelValidationError as exc:
        raise FruitDeepLinksError(str(exc)) from exc
    logger.info(
        "FruitDeepLinks sync (%s): %d channels from %s (%d skipped)",
        mode,
        count,
        base,
        len(skipped),
    )
    return {
        "success": True,
        "imported": count,
        "skipped": skipped,
        "base_url": base,
        "mode": mode,
    }
