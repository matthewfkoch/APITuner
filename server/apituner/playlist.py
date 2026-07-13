"""M3U playlist generation for Channels DVR custom-channel sources."""

from __future__ import annotations

from .models import Channel


def build_m3u(channels: list[Channel], base_url: str) -> str:
    """Build a Channels-DVR-compatible M3U pointing back at APITuner."""
    base = base_url.rstrip("/")
    lines = ["#EXTM3U"]
    for ch in sorted(channels, key=lambda c: c.number):
        attrs = [f'channel-id="{ch.number}"']
        if ch.tvc_guide_stationid:
            attrs.append(f'tvg-id="{ch.tvc_guide_stationid}"')
        attrs.append(f'tvg-name="{_escape(ch.name)}"')
        lines.append(f'#EXTINF:-1 {" ".join(attrs)},{ch.name}')
        lines.append(f"{base}/stream/{ch.number}")
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace('"', "'")
