"""Explicit, opt-in hardware probe; not a production FastChannels resolver.

The preview API is available on existing FastChannels servers, but depends on
admin authentication and does not provide isolated playback sessions. Production
integration must use the external-player contract rather than silently falling
back to this API.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .backends.http_agent import HttpAgentBackend

PLAYER_PACKAGE = "com.fastchannels.player"
PLAYER_COMPONENT = PLAYER_PACKAGE + "/.PlaybackActivity"


class ProbeError(RuntimeError):
    """A sanitized, actionable probe error without credential-bearing URLs."""


def http_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ProbeError("A complete HTTP(S) URL is required")
    if parsed.username or parsed.password:
        raise ProbeError("Do not put credentials in URLs")
    return value


@dataclass(frozen=True)
class PlayerRequest:
    title: str
    channel_key: str
    extras: dict[str, str | bool]


def from_preview(base_url: str, data: Any) -> PlayerRequest:
    """Translate a preview response without executing its bridge trigger URL."""
    base_url = http_url(base_url.rstrip("/") + "/")
    if not isinstance(data, dict) or not isinstance(data.get("channel"), dict):
        raise ProbeError("FastChannels returned an invalid preview response")
    channel = data["channel"]
    source = channel.get("source_name")
    identity = channel.get("source_channel_id")
    title = channel.get("name")
    if not all(isinstance(x, str) and x.strip() for x in (source, identity, title)):
        raise ProbeError("Preview is missing channel identity or title")
    raw_url = data.get("preview_url") or data.get("play_url")
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise ProbeError("Preview did not provide a playback URL")
    stream = http_url(urljoin(base_url, raw_url))
    # These URLs allocate hardware, launch ADB, or return another capture. They
    # are not stream manifests to hand to an Android player.
    if any(segment in urlsplit(stream).path for segment in (
        "/fc-player/", "/prismcast/", "/play/auto/", "/play/tuner/",
    )):
        raise ProbeError("Preview points to a hardware bridge, not a direct player manifest")
    license_url = channel.get("license_url")
    if license_url is not None and not isinstance(license_url, str):
        raise ProbeError("Preview returned an invalid license URL")
    if channel.get("requires_drm_bridge") and not license_url:
        raise ProbeError("Protected channel did not provide a license URL")
    key = f"{source}:{identity}"
    extras: dict[str, str | bool] = {
        "stream_url": stream,
        "title": title,
        "channel_key": key,
        "drm": bool(license_url),
        "captions": False,
        "audio_mode": "stereo",
    }
    if license_url:
        extras["license_url"] = http_url(urljoin(base_url, license_url))
    return PlayerRequest(title=title, channel_key=key, extras=extras)


async def prepare_preview(client: httpx.AsyncClient, base_url: str, channel_id: int) -> PlayerRequest:
    if channel_id <= 0:
        raise ProbeError("Channel ID must be positive")
    base = http_url(base_url).rstrip("/")
    try:
        response = await client.get(f"{base}/api/channels/{channel_id}/preview", timeout=20)
    except httpx.HTTPError:
        raise ProbeError("Could not reach the FastChannels preview API") from None
    if response.status_code == 401:
        raise ProbeError("FastChannels preview requires admin login; use the external-player integration")
    if response.status_code != 200:
        raise ProbeError(f"FastChannels preview returned HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise ProbeError("FastChannels preview did not return JSON") from None
    return from_preview(base, data)


async def prepare_external(
    client: httpx.AsyncClient, base_url: str, channel_key: str, token: str,
) -> PlayerRequest:
    """Use the companion resolve-only API; never fall back to the admin API."""
    if not token:
        raise ProbeError("Set FASTCHANNELS_TOKEN for the external-player API")
    source, separator, identity = channel_key.partition(":")
    if not source or not separator or not identity:
        raise ProbeError("Expected source:channel_id")
    base = http_url(base_url).rstrip("/")
    try:
        response = await client.post(
            base + "/api/external-player/v1/prepare",
            headers={"Authorization": f"Bearer {token}"},
            json={"source": source, "channel_id": identity}, timeout=20,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        raise ProbeError("Could not reach the FastChannels external-player API") from None
    if response.status_code != 200:
        raise ProbeError(f"External-player prepare returned HTTP {response.status_code}; check the companion patch and service token")
    try:
        data = response.json()
    except ValueError:
        raise ProbeError("External-player API did not return JSON") from None
    if (not isinstance(data, dict) or data.get("version") != 1
            or data.get("package") != PLAYER_PACKAGE or data.get("component") != PLAYER_COMPONENT):
        raise ProbeError("Unsupported external-player response")
    extras = data.get("intent_extras")
    if not isinstance(extras, dict) or extras.get("channel_key") != channel_key:
        raise ProbeError("External-player response does not match the requested channel")
    if (type(extras.get("drm")) is not bool or type(extras.get("captions")) is not bool
            or extras.get("audio_mode") not in ("auto", "pcm", "stereo")):
        raise ProbeError("External-player response has invalid playback settings")
    if extras["drm"] != bool(extras.get("license_url")):
        raise ProbeError("External-player response has inconsistent DRM settings")
    # Reuse URL checks and copy only playback fields. Never forward arbitrary
    # command/stop extras received from a resolver to the device.
    prepared = from_preview(base, {
        "channel": {"name": extras.get("title"), "source_name": source,
                    "source_channel_id": identity, "license_url": extras.get("license_url"),
                    "requires_drm_bridge": extras["drm"]},
        "preview_url": extras.get("stream_url"),
    })
    prepared.extras["captions"] = extras["captions"]
    prepared.extras["audio_mode"] = extras["audio_mode"]
    return prepared


async def wait_playing(backend: HttpAgentBackend, timeout: float = 30) -> dict:
    """Do not treat the old Agent's `playing=true, state=buffering` as ready.

    This probes package playback only. The official Player does not yet expose
    session identity or first-frame confirmation through the Agent; verify HDMI
    separately, especially on a same-app channel switch.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        remaining = deadline - asyncio.get_running_loop().time()
        try:
            status = await asyncio.wait_for(backend.playback_details(), remaining)
        except asyncio.TimeoutError:
            break
        if status.get("hasPermission") is False:
            raise ProbeError("Grant Notification Access to APITuner Agent")
        if status.get("package") == PLAYER_PACKAGE and status.get("state") == "playing":
            return status
        await asyncio.sleep(min(0.5, max(0, deadline - asyncio.get_running_loop().time())))
    raise ProbeError("FastChannels Player did not reach PLAYING before the deadline")


async def stop_player(backend: HttpAgentBackend, channel_key: str, timeout: float = 10) -> None:
    await backend.launch(
        package=PLAYER_PACKAGE, component=PLAYER_COMPONENT,
        intent_extras={"command": "warm_stop", "channel_key": channel_key},
    )
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        remaining = deadline - asyncio.get_running_loop().time()
        try:
            status = await asyncio.wait_for(backend.playback_details(), remaining)
        except asyncio.TimeoutError:
            break
        if (status.get("hasPermission") is True
                and status.get("playing") is False
                and status.get("state") in ("none", "stopped", "paused")):
            await backend.stop()
            return
        await asyncio.sleep(min(0.5, max(0, deadline - asyncio.get_running_loop().time())))
    raise ProbeError("Could not confirm Player stop; keep the test tuner disabled")
