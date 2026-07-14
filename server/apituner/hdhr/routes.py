"""HDHomeRun HTTP API routes for Channels DVR / Plex / Emby / Jellyfin."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from .. import __version__
from ..backends import BackendNotPaired, BackendUnavailable
from ..stream import HDHR_SERVER_HEADER, open_hdhr_stream
from ..tuner_manager import NoTunerAvailable, TuneFailed, TunerInUse, TunerManager
from .lineup import (
    build_device_xml,
    build_discover_json,
    build_lineup_json,
    build_lineup_m3u,
    build_lineup_status,
    build_lineup_xml,
    find_channel,
    resolve_base_url,
)
from .xmltv import get_xmltv

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hdhomerun"])


def _store(request: Request):
    return request.app.state.store


def _manager(request: Request) -> TunerManager:
    return request.app.state.manager


def _hdhr_enabled(request: Request) -> bool:
    return bool(_store(request).config.options.hdhr_enabled)


def _require_hdhr(request: Request) -> None:
    if not _hdhr_enabled(request):
        raise HTTPException(status_code=404, detail="HDHomeRun emulation disabled")


def _base_url(request: Request) -> str:
    options = _store(request).config.options
    return resolve_base_url(str(request.base_url), options)


def _json(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        content=data,
        status_code=status_code,
        headers={"Server": HDHR_SERVER_HEADER},
    )


def _error(
    status_code: int, message: str, *, hdhr_code: Optional[int] = None
) -> Response:
    headers = {"Server": HDHR_SERVER_HEADER}
    if hdhr_code is not None:
        headers["X-HDHomeRun-Error"] = str(hdhr_code)
    return PlainTextResponse(message, status_code=status_code, headers=headers)


# ---- Discovery / identity ----


@router.get("/discover.json", include_in_schema=False)
@router.get("/device.json", include_in_schema=False)
async def discover_json(request: Request) -> Response:
    _require_hdhr(request)
    store = _store(request)
    manager = _manager(request)
    return _json(
        build_discover_json(
            base_url=_base_url(request),
            options=store.config.options,
            tuner_count=manager.tuner_count(),
            firmware_version=__version__,
        )
    )


@router.get("/device.xml", include_in_schema=False)
async def device_xml(request: Request) -> Response:
    _require_hdhr(request)
    store = _store(request)
    manager = _manager(request)
    xml = build_device_xml(
        base_url=_base_url(request),
        options=store.config.options,
        tuner_count=manager.tuner_count(),
    )
    return Response(
        content=xml,
        media_type="text/xml",
        headers={"Server": HDHR_SERVER_HEADER},
    )


# ---- Lineup ----


@router.get("/lineup.json", include_in_schema=False)
async def lineup_json(request: Request) -> Response:
    """Return channel lineup. Accepts Channels query params: tuning, show=found|all."""
    _require_hdhr(request)
    # Query params are intentionally ignored — channels are statically configured.
    _ = request.query_params.get("show")
    _ = request.query_params.get("tuning")
    channels = _store(request).config.channels
    return _json(build_lineup_json(channels, _base_url(request)))


@router.get("/lineup.xml", include_in_schema=False)
async def lineup_xml(request: Request) -> Response:
    _require_hdhr(request)
    channels = _store(request).config.channels
    return Response(
        content=build_lineup_xml(channels, _base_url(request)),
        media_type="text/xml",
        headers={"Server": HDHR_SERVER_HEADER},
    )


@router.get("/lineup.m3u", include_in_schema=False)
async def lineup_m3u(request: Request) -> Response:
    _require_hdhr(request)
    channels = _store(request).config.channels
    return PlainTextResponse(
        build_lineup_m3u(channels, _base_url(request)),
        media_type="audio/x-mpegurl",
        headers={"Server": HDHR_SERVER_HEADER},
    )


@router.get("/lineup_status.json", include_in_schema=False)
async def lineup_status(request: Request) -> Response:
    _require_hdhr(request)
    return _json(build_lineup_status())


@router.post("/lineup.post", include_in_schema=False)
async def lineup_post(request: Request) -> Response:
    """Acknowledge scan-start requests from Plex / similar clients."""
    _require_hdhr(request)
    return Response(status_code=200, headers={"Server": HDHR_SERVER_HEADER})


@router.get("/xmltv.xml", include_in_schema=False)
@router.get("/epg.xml", include_in_schema=False)
async def xmltv_epg(
    request: Request,
    duration: int | None = None,
    refresh: int = 0,
) -> Response:
    """XMLTV EPG remapped from Channels DVR via Gracenote StationIDs.

    Point Channels DVR's HDHomeRun "Custom URL" guide provider at this endpoint.
    Requires options.channels_dvr_url (Channels DVR base URL on your LAN).
    """
    _require_hdhr(request)
    store = _store(request)
    try:
        xml = await get_xmltv(
            store.config.channels,
            store.config.options,
            duration_override=duration,
            force_refresh=bool(refresh),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("XMLTV build failed")
        raise HTTPException(
            status_code=502, detail=f"Failed to build XMLTV: {exc}"
        ) from exc
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Server": HDHR_SERVER_HEADER},
    )


# ---- Streaming ----


async def _stream_channel(
    request: Request, channel_token: str, *, tuner_index: Optional[int] = None
) -> Response:
    _require_hdhr(request)
    store = _store(request)
    manager = _manager(request)
    channel = find_channel(store.config.channels, channel_token)
    if channel is None:
        return _error(404, f"Unknown channel {channel_token}", hdhr_code=801)
    try:
        return await open_hdhr_stream(
            request, manager, channel, tuner_index=tuner_index
        )
    except TunerInUse as exc:
        return _error(503, str(exc), hdhr_code=804)
    except NoTunerAvailable as exc:
        return _error(503, str(exc), hdhr_code=805)
    except (TuneFailed, BackendUnavailable) as exc:
        return _error(503, str(exc), hdhr_code=806)
    except BackendNotPaired as exc:
        return _error(503, str(exc), hdhr_code=806)


@router.get("/auto/v{channel_token}", include_in_schema=False)
async def auto_tune(channel_token: str, request: Request) -> Response:
    return await _stream_channel(request, channel_token, tuner_index=None)


@router.get("/tuner{tuner_index}/v{channel_token}", include_in_schema=False)
async def tuner_tune(
    tuner_index: int, channel_token: str, request: Request
) -> Response:
    if tuner_index < 0:
        return _error(404, f"Unknown tuner {tuner_index}", hdhr_code=801)
    return await _stream_channel(
        request, channel_token, tuner_index=tuner_index
    )


# ---- Status ----


@router.get("/status.json", include_in_schema=False)
async def device_status(request: Request) -> Response:
    _require_hdhr(request)
    manager = _manager(request)
    enabled = manager.enabled_tuners()
    out: list[dict[str, Any]] = []
    status_by_id = {s["id"]: s for s in manager.status()}
    for i, tuner in enumerate(enabled):
        st = status_by_id.get(tuner.id, {})
        entry: dict[str, Any] = {"Resource": f"tuner{i}"}
        if st.get("locked"):
            entry.update(
                {
                    "Frequency": 0,
                    "SignalQualityPercent": 100,
                    "SignalStrengthPercent": 100,
                    "SymbolQualityPercent": 100,
                }
            )
            if st.get("channel_name"):
                entry["VctName"] = st["channel_name"]
            if st.get("channel_number") is not None:
                entry["VctNumber"] = str(st["channel_number"])
        out.append(entry)
    return _json(out)


@router.get("/tuner{tuner_index}/status.json", include_in_schema=False)
async def tuner_status(tuner_index: int, request: Request) -> Response:
    _require_hdhr(request)
    manager = _manager(request)
    tuner = manager.tuner_at_index(tuner_index)
    if tuner is None:
        raise HTTPException(status_code=404, detail=f"Unknown tuner {tuner_index}")
    st = next((s for s in manager.status() if s["id"] == tuner.id), {})
    entry: dict[str, Any] = {"Resource": f"tuner{tuner_index}"}
    if st.get("locked"):
        entry.update(
            {
                "Frequency": 0,
                "SignalQualityPercent": 100,
                "SignalStrengthPercent": 100,
                "SymbolQualityPercent": 100,
            }
        )
        if st.get("channel_name"):
            entry["VctName"] = st["channel_name"]
        if st.get("channel_number") is not None:
            entry["VctNumber"] = str(st["channel_number"])
    return _json(entry)
