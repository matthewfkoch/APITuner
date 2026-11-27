"""Dashboard, M3U playlists, direct stream route, discovery, status, and diagnostics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from .. import __version__
from ..agent_update import AgentUpdateError
from ..backends import BackendNotPaired, BackendUnavailable
from ..channels import resolve_channel
from ..diagnostics import build_diagnostics
from ..discovery import discover
from ..hdhr.lineup import resolve_base_url
from ..playlist import build_m3u, filter_channels_by_provider
from ..stream import open_stream
from ..tuner_manager import NoTunerAvailable, TuneFailed
from . import get_manager, get_store

router = APIRouter()


@router.get("/", include_in_schema=False)
async def dashboard() -> Response:
    from .. import main as app_main

    index = app_main.WEB_DIR / "index.html"
    if index.exists():
        html = (
            index.read_text(encoding="utf-8")
            .replace("{{AGENT_APK_RELEASES_URL}}", app_main.AGENT_APK_RELEASES_URL)
            .replace("{{VERSION}}", __version__)
        )
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-store"},
        )
    return PlainTextResponse("APITuner is running. Dashboard assets missing.")


def _channels_playlist(request: Request, provider: str | None = None) -> Response:
    channels = filter_channels_by_provider(
        get_store(request).config.channels, provider
    )
    base_url = str(request.base_url)
    return PlainTextResponse(build_m3u(channels, base_url), media_type="audio/x-mpegurl")


@router.get("/channels.m3u", include_in_schema=False)
async def channels_m3u(request: Request, provider: str | None = None) -> Response:
    return _channels_playlist(request, provider)


@router.get("/channels.m3u8", include_in_schema=False)
async def channels_m3u8(request: Request, provider: str | None = None) -> Response:
    return _channels_playlist(request, provider)


@router.get("/stream/{token}", include_in_schema=False)
async def stream(token: str, request: Request) -> Response:
    store = get_store(request)
    manager = get_manager(request)
    channel, err = resolve_channel(store.config.channels, token)
    if err == "ambiguous":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Channel number {token} matches multiple channels; "
                "use the stream URL from channels.m3u"
            ),
        )
    if channel is None:
        raise HTTPException(status_code=404, detail=f"Unknown channel {token}")
    try:
        return await open_stream(request, manager, channel)
    except NoTunerAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (TuneFailed, BackendUnavailable) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except BackendNotPaired as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/discover")
async def discover_devices(timeout: float = 5.0) -> list[dict[str, Any]]:
    return await discover(timeout=min(timeout, 15.0))


@router.get("/api/agent/latest")
async def agent_latest(force: bool = False) -> dict[str, Any]:
    """Return the cached public Agent APK latest.json manifest."""
    from .. import main as app_main

    try:
        latest = await app_main.latest_cache.get(force=force)
    except AgentUpdateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return latest.to_dict()


@router.get("/api/status")
async def status(request: Request) -> dict:
    from .. import main as app_main

    store = get_store(request)
    manager = get_manager(request)
    options = store.config.options
    request_base = str(request.base_url).rstrip("/")
    base = resolve_base_url(request_base, options)
    xmltv_url = f"{base}/xmltv.xml" if options.hdhr_enabled else None
    m3u_url = f"{request_base}/channels.m3u8"
    epg_source = bool(
        (options.channels_dvr_url or "").strip()
        or (options.fruitdeeplinks_url or "").strip()
        or (options.fastchannels_url or "").strip()
    )
    return {
        "version": __version__,
        "agent_apk_url": app_main.AGENT_APK_RELEASES_URL,
        "agent_latest_url": app_main.latest_cache.url,
        "m3u_url": m3u_url,
        "options": options.model_dump(),
        "tuners": manager.status(),
        "channel_count": len(store.config.channels),
        "hdhr": {
            "enabled": options.hdhr_enabled,
            "friendly_name": options.hdhr_friendly_name,
            "device_id": options.hdhr_device_id,
            "tuner_count": manager.tuner_count(),
            "base_url": base if options.hdhr_enabled else None,
            "discover_url": f"{base}/discover.json" if options.hdhr_enabled else None,
            "xmltv_url": xmltv_url,
            "epg_source": epg_source,
            "channels_dvr_url": options.channels_dvr_url,
            "ssdp_enabled": options.hdhr_ssdp_enabled,
            "udp_discovery_enabled": options.hdhr_udp_discovery_enabled,
            "discovery_running": getattr(request.app.state, "hdhr_discovery", None)
            is not None,
        },
    }


@router.get("/api/diagnostics")
async def diagnostics(request: Request) -> Response:
    """Downloadable support bundle (tokens redacted; may include LAN IPs)."""
    bundle = await build_diagnostics(get_store(request), get_manager(request))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    body = json.dumps(bundle, indent=2, sort_keys=False)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="apituner-diagnostics-{stamp}.json"'
        },
    )
