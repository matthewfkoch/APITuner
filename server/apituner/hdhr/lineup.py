"""HDHomeRun discover.json / lineup.json builders."""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape

from ..models import Channel, GlobalOptions

# Widely-recognized CONNECT DUO model string used by most HDHR emulators.
HDHR_MODEL = "HDTC-2US"
HDHR_FIRMWARE_NAME = "hdhomeruntc_atsc"


def resolve_base_url(request_base_url: str, options: GlobalOptions) -> str:
    """Build the BaseURL advertised to HDHR clients.

    When hdhr_port is set, advertise that port even if the request arrived on
    the main APITuner port (e.g. dashboard previewing discover.json).
    """
    base = request_base_url.rstrip("/")
    if options.hdhr_port is None:
        return base
    # Replace the port in the request base URL with the configured HDHR port.
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(base)
    host = parts.hostname or "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{options.hdhr_port}"
    return urlunsplit((parts.scheme or "http", netloc, "", "", "")).rstrip("/")


def build_discover_json(
    *,
    base_url: str,
    options: GlobalOptions,
    tuner_count: int,
    firmware_version: str,
) -> dict[str, Any]:
    """Build discover.json / device.json payload."""
    base = base_url.rstrip("/")
    device_id = options.hdhr_device_id.upper()
    return {
        "FriendlyName": options.hdhr_friendly_name,
        "ModelNumber": HDHR_MODEL,
        "FirmwareName": HDHR_FIRMWARE_NAME,
        "FirmwareVersion": firmware_version,
        "DeviceID": device_id,
        "DeviceAuth": device_id,
        "BaseURL": base,
        "LineupURL": f"{base}/lineup.json",
        "Manufacturer": "APITuner",
        "TunerCount": max(0, tuner_count),
    }


def build_lineup_json(channels: list[Channel], base_url: str) -> list[dict[str, Any]]:
    """Build lineup.json entries pointing at /auto/v{number} stream URLs."""
    base = base_url.rstrip("/")
    out: list[dict[str, Any]] = []
    for ch in sorted(channels, key=lambda c: c.number):
        entry: dict[str, Any] = {
            "GuideNumber": str(ch.number),
            "GuideName": ch.name,
            "HD": 1,
            "URL": f"{base}/auto/v{ch.number}",
        }
        if ch.tvc_guide_stationid:
            entry["StationID"] = ch.tvc_guide_stationid
        out.append(entry)
    return out


def build_lineup_xml(channels: list[Channel], base_url: str) -> str:
    """Minimal lineup.xml for clients that prefer XML."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<Lineup>"]
    for entry in build_lineup_json(channels, base_url):
        lines.append("  <Program>")
        lines.append(f"    <GuideNumber>{escape(entry['GuideNumber'])}</GuideNumber>")
        lines.append(f"    <GuideName>{escape(entry['GuideName'])}</GuideName>")
        lines.append(f"    <URL>{escape(entry['URL'])}</URL>")
        lines.append("  </Program>")
    lines.append("</Lineup>")
    return "\n".join(lines) + "\n"


def build_lineup_m3u(channels: list[Channel], base_url: str) -> str:
    """HDHomeRun-style lineup.m3u."""
    base = base_url.rstrip("/")
    lines = ["#EXTM3U"]
    for ch in sorted(channels, key=lambda c: c.number):
        lines.append(f'#EXTINF:-1 tvg-chno="{ch.number}",{ch.name}')
        lines.append(f"{base}/auto/v{ch.number}")
    return "\n".join(lines) + "\n"


def build_device_xml(
    *,
    base_url: str,
    options: GlobalOptions,
    tuner_count: int,
) -> str:
    """Minimal UPnP device.xml for SSDP / Plex discovery."""
    base = base_url.rstrip("/")
    device_id = options.hdhr_device_id.upper()
    friendly = escape(options.hdhr_friendly_name)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion>
    <major>1</major>
    <minor>0</minor>
  </specVersion>
  <URLBase>{escape(base)}</URLBase>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>{friendly}</friendlyName>
    <manufacturer>Silicondust</manufacturer>
    <modelName>{HDHR_MODEL}</modelName>
    <modelNumber>{HDHR_MODEL}</modelNumber>
    <serialNumber>{escape(device_id)}</serialNumber>
    <UDN>uuid:{escape(device_id)}</UDN>
    <tunerCount>{max(0, tuner_count)}</tunerCount>
  </device>
</root>
"""


def build_lineup_status() -> dict[str, Any]:
    """Static scan-complete response so DVR clients do not stall."""
    return {
        "ScanInProgress": 0,
        "ScanPossible": 1,
        "Source": "Cable",
        "SourceList": ["Cable"],
    }


def parse_channel_token(token: str) -> str:
    """Normalize /auto/v{token} channel token (supports dotted ATSC-style).

    Returns the string form used to look up channels. Prefer exact integer
    match first; dotted forms (e.g. \"5.1\") are returned as-is for lookup.
    """
    return token.strip()


def find_channel(channels: list[Channel], token: str) -> Channel | None:
    """Resolve a GuideNumber token to a configured Channel."""
    token = parse_channel_token(token)
    if not token:
        return None
    # Exact numeric match (\"9000\" or \"9000.0\").
    for ch in channels:
        if str(ch.number) == token:
            return ch
    # Dotted major.minor mapped to an integer channel number \"major\".
    if "." in token:
        major, _, minor = token.partition(".")
        if major.isdigit() and (not minor or minor.isdigit()):
            for ch in channels:
                if str(ch.number) == major:
                    return ch
                # Also allow storing as \"5.1\" via name-only if number was int-rounded.
                if f"{ch.number}.{minor}" == token and minor:
                    return ch
    return None
