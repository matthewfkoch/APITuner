"""M3U playlist generation for Channels DVR custom-channel sources."""

from __future__ import annotations

from .channels import sort_channels
from .deeplink_catalog import parse_whatson_url
from .models import Channel

SOURCE_YTTV_SPORTS = "yttv-sports"


def filter_channels_by_provider(
    channels: list[Channel], provider: str | None
) -> list[Channel]:
    """Filter channels by provider_name (ADBTuner ?provider= query param)."""
    if not provider:
        return channels
    want = provider.strip().casefold()
    return [
        ch
        for ch in channels
        if ch.provider_name and ch.provider_name.strip().casefold() == want
    ]


def m3u_tvg_id(channel: Channel) -> str:
    """XMLTV join key for Channels DVR (tvg-id). Not Gracenote."""
    explicit = (channel.tvg_id or "").strip()
    if explicit:
        return explicit
    lane = parse_whatson_url(channel.url or "")
    source = (channel.source or "").strip()
    if lane is not None and source == SOURCE_YTTV_SPORTS:
        return f"{SOURCE_YTTV_SPORTS}-{lane}"
    return ""


def build_m3u(channels: list[Channel], base_url: str) -> str:
    """Build a Channels-DVR-compatible M3U pointing back at APITuner."""
    base = base_url.rstrip("/")
    lines = ["#EXTM3U"]
    for ch in sort_channels(channels):
        attrs = [
            f'channel-id="{ch.id}"',
            f'channel-number="{ch.number}"',
            f'tvg-chno="{ch.number}"',
        ]
        tvg_id = m3u_tvg_id(ch)
        if tvg_id:
            attrs.append(f'tvg-id="{_escape(tvg_id)}"')
        if ch.tvc_guide_stationid:
            # Channels DVR reads Gracenote IDs from tvc-guide-stationid (not tvg-id).
            attrs.append(f'tvc-guide-stationid="{ch.tvc_guide_stationid}"')
        attrs.append(f'tvg-name="{_escape(ch.name)}"')
        lines.append(f'#EXTINF:-1 {" ".join(attrs)},{ch.name}')
        lines.append(f"{base}/stream/{ch.id}")
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace('"', "'")
