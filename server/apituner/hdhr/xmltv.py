"""Build XMLTV EPG from Channels DVR guide data remapped via Gracenote StationIDs."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional
from xml.sax.saxutils import escape

import httpx

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


async def get_xmltv(
    channels: list[Channel],
    options: GlobalOptions,
    *,
    duration_override: Optional[int] = None,
    force_refresh: bool = False,
) -> str:
    """Return cached or freshly built XMLTV."""
    dvr = (options.channels_dvr_url or "").strip().rstrip("/")
    if not dvr:
        raise ValueError(
            "channels_dvr_url is not set. Set it in Options to your Channels DVR "
            "base URL (e.g. http://192.0.2.30:8089)."
        )

    duration = int(duration_override or options.xmltv_duration_seconds)
    device = options.xmltv_source_device or "M3U-YouTubeTV"
    cache_key = f"{dvr}|{device}|{duration}|{len(channels)}"
    now = time.time()
    if (
        not force_refresh
        and _cache["key"] == cache_key
        and _cache["xml"]
        and _cache["expires"] > now
    ):
        return _cache["xml"]

    guide = await fetch_channels_guide(
        dvr_url=dvr,
        device_id=device,
        duration_seconds=duration,
    )
    xml = build_xmltv(channels, guide)
    _cache["key"] = cache_key
    _cache["xml"] = xml
    _cache["expires"] = now + max(30.0, float(options.xmltv_cache_seconds))
    return xml
