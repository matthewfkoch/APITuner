"""Build XMLTV EPG from Channels DVR guide data remapped via Gracenote StationIDs."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional
from urllib.parse import urljoin
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import httpx

from ..deeplink_catalog import parse_lane_url, parse_whatson_url
from ..m3u_import import SOURCE_FRUITDEEPLINKS
from ..models import Channel, GlobalOptions

logger = logging.getLogger(__name__)

_cache: dict[str, Any] = {"key": None, "xml": None, "expires": 0.0}


def _xmltv_ts(unix_ts: int) -> str:
    return time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(unix_ts))


def _station_from_guide_entry(entry: dict[str, Any]) -> Optional[str]:
    ch = entry.get("Channel") or {}
    station = ch.get("Station") or ch.get("stationId")
    if station:
        return str(station)
    airs = entry.get("Airings") or []
    if airs:
        raw = airs[0].get("Raw") or {}
        if raw.get("stationId"):
            return str(raw["stationId"])
    return None


def _text(tag: str, value: str) -> str:
    return f"<{tag}>{escape(value)}</{tag}>"


async def fetch_channels_guide(
    *,
    dvr_url: str,
    device_id: str,
    duration_seconds: int,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """Fetch JSON guide from a Channels DVR device endpoint."""
    base = dvr_url.rstrip("/")
    url = f"{base}/devices/{device_id}/guide?duration={int(duration_seconds)}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected guide payload type: {type(data)}")
    return data


def build_xmltv(
    channels: list[Channel],
    guide_entries: list[dict[str, Any]],
    *,
    generator_name: str = "APITuner",
) -> str:
    """Remap Channels DVR airings onto APITuner channel numbers via StationID."""
    station_to_number: dict[str, int] = {}
    for ch in channels:
        if ch.tvc_guide_stationid:
            station_to_number[str(ch.tvc_guide_stationid)] = ch.number

    # Guide airings keyed by station id.
    airings_by_station: dict[str, list[dict[str, Any]]] = {}
    for entry in guide_entries:
        station = _station_from_guide_entry(entry)
        if not station or station not in station_to_number:
            continue
        for airing in entry.get("Airings") or []:
            airings_by_station.setdefault(station, []).append(airing)

    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
        f'<tv generator-info-name="{escape(generator_name)}">',
    ]

    for ch in sorted(channels, key=lambda c: c.number):
        cid = str(ch.number)
        lines.append(f'  <channel id="{escape(cid)}">')
        lines.append(f"    {_text('display-name', cid)}")
        lines.append(f"    {_text('display-name', ch.name)}")
        lines.append(f"    {_text('lcn', cid)}")
        if ch.tvc_guide_stationid:
            # Non-standard but useful breadcrumb for debugging.
            lines.append(
                f'    {_text("display-name", f"station-{ch.tvc_guide_stationid}")}'
            )
        lines.append("  </channel>")

    programme_count = 0
    for ch in sorted(channels, key=lambda c: c.number):
        station = str(ch.tvc_guide_stationid) if ch.tvc_guide_stationid else ""
        if not station:
            continue
        for airing in airings_by_station.get(station, []):
            start = airing.get("Time")
            duration = airing.get("Duration")
            title = airing.get("Title") or "Unknown"
            if not isinstance(start, int) or not isinstance(duration, int):
                continue
            stop = start + duration
            cid = str(ch.number)
            lines.append(
                f'  <programme start="{_xmltv_ts(start)}" stop="{_xmltv_ts(stop)}" '
                f'channel="{escape(cid)}">'
            )
            lines.append(f"    {_text('title', str(title))}")
            if airing.get("EpisodeTitle"):
                lines.append(f"    {_text('sub-title', str(airing['EpisodeTitle']))}")
            if airing.get("Summary"):
                lines.append(f"    {_text('desc', str(airing['Summary']))}")
            elif airing.get("Raw") and (airing["Raw"].get("program") or {}).get(
                "longDescription"
            ):
                lines.append(
                    f"    {_text('desc', str(airing['Raw']['program']['longDescription']))}"
                )
            for cat in airing.get("Categories") or []:
                lines.append(f"    {_text('category', str(cat))}")
            if airing.get("Image"):
                lines.append(f'    <icon src="{escape(str(airing["Image"]))}" />')
            if airing.get("SeriesID"):
                lines.append(
                    f'    <series-id system="tms">{escape(str(airing["SeriesID"]))}</series-id>'
                )
            if airing.get("ProgramID"):
                lines.append(
                    f'    <episode-num system="tms">{escape(str(airing["ProgramID"]))}</episode-num>'
                )
            if airing.get("OriginalDate"):
                # YYYY-MM-DD -> YYYYMMDD
                date = str(airing["OriginalDate"]).replace("-", "")[:8]
                if date:
                    lines.append(f"    {_text('date', date)}")
            lines.append("  </programme>")
            programme_count += 1

    lines.append("</tv>")
    lines.append("")
    logger.info(
        "Built XMLTV: %d channels, %d programmes (%d stations matched)",
        len(channels),
        programme_count,
        len(airings_by_station),
    )
    return "\n".join(lines)


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1]


def fdl_xmltv_aliases(channel: Channel) -> set[str]:
    """IDs that FruitDeepLinks XMLTV might use for this APITuner channel."""
    aliases = {str(channel.number).lower()}
    if channel.name:
        aliases.add(channel.name.strip().lower())
    if channel.provider_name:
        aliases.add(channel.provider_name.strip().lower())
    lane = parse_lane_url(channel.url or "")
    if lane:
        code, number = lane
        aliases.update(
            {
                f"adb-{code}-{number:03d}",
                f"adb-{code}-{number}",
                f"{code}-{number:03d}",
                f"{code}-{number}",
                code,
            }
        )
    whatson = parse_whatson_url(channel.url or "")
    if whatson is not None:
        aliases.update(
            {
                f"lane.{whatson}",
                f"fruit lane {whatson}",
                str(whatson),
            }
        )
    return {a for a in aliases if a}


def fdl_channel_id_map(channels: list[Channel]) -> dict[str, int]:
    """Map foreign XMLTV channel ids onto APITuner channel numbers."""
    mapping: dict[str, int] = {}
    for ch in channels:
        url = ch.url or ""
        sourced = (ch.source or "").strip() == SOURCE_FRUITDEEPLINKS
        if not sourced and "/api/adb/lanes/" not in url and "/whatson/" not in url:
            continue
        for alias in fdl_xmltv_aliases(ch):
            mapping[alias] = ch.number
    return mapping


def rewrite_fdl_xmltv(xml_text: str, channels: list[Channel]) -> str:
    """Rewrite FDL <channel id> / programme channel= onto APITuner numbers.

    Returns only the remapped ``<programme>`` elements (channel list stays
    with ``build_xmltv``). Unmatched programmes are dropped.
    """
    mapping = fdl_channel_id_map(channels)
    if not xml_text.strip() or not mapping:
        return ""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("FruitDeepLinks XMLTV parse failed: %s", exc)
        return ""

    # Prefer explicit channel-id on FDL channel records (display-name fallback).
    id_to_number: dict[str, int] = {}
    for child in list(root):
        if _local_tag(child.tag) != "channel":
            continue
        raw_id = (child.get("id") or "").strip()
        candidates = [raw_id]
        for dn in child:
            if _local_tag(dn.tag) == "display-name" and (dn.text or "").strip():
                candidates.append(dn.text.strip())
        for cand in candidates:
            key = cand.lower()
            if key in mapping:
                id_to_number[raw_id] = mapping[key]
                break

    snippets: list[str] = []
    for child in list(root):
        if _local_tag(child.tag) != "programme":
            continue
        foreign = (child.get("channel") or "").strip()
        number = id_to_number.get(foreign)
        if number is None:
            number = mapping.get(foreign.lower())
        if number is None:
            continue
        child.set("channel", str(number))
        snippets.append(ET.tostring(child, encoding="unicode"))
    return "\n".join(snippets)


def merge_fdl_programmes(xmltv: str, programme_xml: str) -> str:
    """Insert remapped programme elements before the closing ``</tv>`` tag."""
    extra = (programme_xml or "").strip()
    if not extra:
        return xmltv
    marker = "</tv>"
    idx = xmltv.rfind(marker)
    if idx < 0:
        return xmltv.rstrip() + "\n" + extra + "\n</tv>\n"
    return xmltv[:idx] + extra + "\n" + xmltv[idx:]


async def fetch_fdl_xmltv(options: GlobalOptions, *, timeout: float = 30.0) -> Optional[str]:
    """Fetch FruitDeepLinks XMLTV; try configured path then common fallbacks."""
    base = (options.fruitdeeplinks_url or "").strip().rstrip("/")
    if not base:
        return None
    primary = options.fruitdeeplinks_xmltv_path or "/xmltv/adb"
    paths: list[str] = []
    for path in (primary, "/xmltv/adb", "/xmltv/lanes", "/xmltv/direct"):
        if path and path not in paths:
            paths.append(path)
    last_error: Optional[str] = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        for path in paths:
            url = urljoin(base + "/", path.lstrip("/"))
            try:
                resp = await client.get(url)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning("FruitDeepLinks XMLTV fetch %s failed: %s", url, exc)
                continue
            if resp.status_code >= 400:
                last_error = f"HTTP {resp.status_code}"
                continue
            body = (resp.text or "").strip()
            if body.startswith("<"):
                return body
            last_error = "not XML"
    if last_error:
        logger.warning("FruitDeepLinks XMLTV unavailable: %s", last_error)
    return None


async def get_xmltv(
    channels: list[Channel],
    options: GlobalOptions,
    *,
    duration_override: Optional[int] = None,
    force_refresh: bool = False,
) -> str:
    """Return cached or freshly built XMLTV (Gracenote remap and/or FDL lanes)."""
    dvr = (options.channels_dvr_url or "").strip().rstrip("/")
    fdl = (options.fruitdeeplinks_url or "").strip().rstrip("/")
    if not dvr and not fdl:
        raise ValueError(
            "Set Channels DVR URL (Gracenote remap) and/or FruitDeepLinks URL "
            "in Options so /xmltv.xml has a guide source."
        )

    duration = int(duration_override or options.xmltv_duration_seconds)
    device = options.xmltv_source_device or "M3U-YouTubeTV"
    numbers = ",".join(str(ch.number) for ch in sorted(channels, key=lambda c: c.number))
    cache_key = (
        f"{dvr}|{fdl}|{options.fruitdeeplinks_xmltv_path}|{device}|{duration}|{numbers}"
    )
    now = time.time()
    if (
        not force_refresh
        and _cache["key"] == cache_key
        and _cache["xml"]
        and _cache["expires"] > now
    ):
        return _cache["xml"]

    guide: list[dict[str, Any]] = []
    if dvr:
        try:
            guide = await fetch_channels_guide(
                dvr_url=dvr,
                device_id=device,
                duration_seconds=duration,
            )
        except Exception:
            if not fdl:
                raise
            logger.exception(
                "Channels DVR guide fetch failed; continuing with FDL XMLTV"
            )

    xml = build_xmltv(channels, guide)
    if fdl:
        foreign = await fetch_fdl_xmltv(options)
        if foreign:
            xml = merge_fdl_programmes(xml, rewrite_fdl_xmltv(foreign, channels))
    _cache["key"] = cache_key
    _cache["xml"] = xml
    _cache["expires"] = now + max(30.0, float(options.xmltv_cache_seconds))
    return xml
