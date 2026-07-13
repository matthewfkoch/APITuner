"""APITuner FastAPI application: dashboard, M3U, streaming, and management API."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .backends import BackendNotPaired, BackendUnavailable
from .config import ConfigStore
from .discovery import discover
from .models import Channel, GlobalOptions, Tuner
from .playlist import build_m3u
from .stream import open_stream
from .tuner_manager import NoTunerAvailable, TuneFailed, TunerManager

logging.basicConfig(
    level=os.environ.get("APITUNER_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("apituner")

WEB_DIR = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = ConfigStore()
    manager = TunerManager(store)
    app.state.store = store
    app.state.manager = manager
    await manager.start_reaper()
    # Best-effort: warm device info so tuner selection is app-aware.
    for tuner in store.config.tuners:
        if tuner.enabled:
            try:
                await manager.refresh_info(tuner.id)
            except Exception:  # noqa: BLE001
                pass
    logger.info("APITuner %s started", __version__)
    try:
        yield
    finally:
        await manager.stop_reaper()


app = FastAPI(title="APITuner", version=__version__, lifespan=lifespan)


def _store(request: Request) -> ConfigStore:
    return request.app.state.store


def _manager(request: Request) -> TunerManager:
    return request.app.state.manager


# ---- Dashboard + playlist + streaming ----


@app.get("/", include_in_schema=False)
async def dashboard() -> Response:
    index = WEB_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return PlainTextResponse("APITuner is running. Dashboard assets missing.")


@app.get("/channels.m3u", include_in_schema=False)
async def channels_m3u(request: Request) -> Response:
    channels = _store(request).config.channels
    base_url = str(request.base_url)
    return PlainTextResponse(build_m3u(channels, base_url), media_type="audio/x-mpegurl")


@app.get("/stream/{number}", include_in_schema=False)
async def stream(number: int, request: Request) -> Response:
    store = _store(request)
    manager = _manager(request)
    channel = next((c for c in store.config.channels if c.number == number), None)
    if channel is None:
        raise HTTPException(status_code=404, detail=f"Unknown channel {number}")
    try:
        return await open_stream(request, manager, channel)
    except NoTunerAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (TuneFailed, BackendUnavailable) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except BackendNotPaired as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---- Tuner management ----


@app.get("/api/tuners")
async def list_tuners(request: Request) -> list[dict]:
    return [t.model_dump() for t in _store(request).config.tuners]


@app.post("/api/tuners")
async def create_tuner(tuner: Tuner, request: Request) -> dict:
    store = _store(request)
    store.config.tuners.append(tuner)
    store.save()
    return tuner.model_dump()


@app.put("/api/tuners/{tuner_id}")
async def update_tuner(tuner_id: str, tuner: Tuner, request: Request) -> dict:
    store = _store(request)
    manager = _manager(request)
    idx = next(
        (i for i, t in enumerate(store.config.tuners) if t.id == tuner_id), None
    )
    if idx is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    tuner.id = tuner_id
    store.config.tuners[idx] = tuner
    store.save()
    await manager.invalidate(tuner_id)
    return tuner.model_dump()


@app.delete("/api/tuners/{tuner_id}")
async def delete_tuner(tuner_id: str, request: Request) -> dict:
    store = _store(request)
    manager = _manager(request)
    before = len(store.config.tuners)
    store.config.tuners = [t for t in store.config.tuners if t.id != tuner_id]
    if len(store.config.tuners) == before:
        raise HTTPException(status_code=404, detail="Tuner not found")
    store.save()
    await manager.invalidate(tuner_id)
    return {"success": True}


@app.get("/api/tuners/{tuner_id}/health")
async def tuner_health(tuner_id: str, request: Request) -> dict:
    return {"online": await _manager(request).health(tuner_id)}


@app.get("/api/tuners/{tuner_id}/info")
async def tuner_info(tuner_id: str, request: Request) -> dict:
    info = await _manager(request).refresh_info(tuner_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Tuner not found or unreachable")
    return {
        "model": info.model,
        "manufacturer": info.manufacturer,
        "os_version": info.os_version,
        "sdk_int": info.sdk_int,
        "packages": info.packages,
    }


@app.get("/api/tuners/{tuner_id}/apps")
async def tuner_apps(tuner_id: str, request: Request) -> list[dict]:
    manager = _manager(request)
    tuner = next((t for t in _store(request).config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    backend = manager.get_backend(tuner)
    # Prefer a rich name+package list from the Agent when available.
    if hasattr(backend, "list_apps"):
        try:
            return await backend.list_apps()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    info = await manager.refresh_info(tuner_id)
    if info is None:
        return []
    return [{"name": p, "packageName": p} for p in info.packages]


# ---- Pairing (androidtv_remote backend) ----


@app.post("/api/tuners/{tuner_id}/pair/start")
async def pair_start(tuner_id: str, request: Request) -> dict:
    manager = _manager(request)
    tuner = next((t for t in _store(request).config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    backend = manager.get_backend(tuner)
    if not backend.requires_pairing:
        raise HTTPException(status_code=400, detail="Backend does not require pairing")
    try:
        await backend.start_pairing()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Pairing failed: {exc}") from exc
    return {"success": True, "message": "Enter the PIN shown on the TV"}


@app.post("/api/tuners/{tuner_id}/pair/finish")
async def pair_finish(tuner_id: str, request: Request) -> dict:
    manager = _manager(request)
    tuner = next((t for t in _store(request).config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    body = await request.json()
    pin = str(body.get("pin", "")).strip()
    if not pin:
        raise HTTPException(status_code=400, detail="Missing pin")
    backend = manager.get_backend(tuner)
    try:
        await backend.finish_pairing(pin)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Pairing failed: {exc}") from exc
    await manager.refresh_info(tuner_id)
    return {"success": True, "message": "Paired successfully"}


# ---- Channel management ----


@app.get("/api/channels")
async def list_channels(request: Request) -> list[dict]:
    return [c.model_dump() for c in _store(request).config.channels]


@app.post("/api/channels")
async def create_channel(channel: Channel, request: Request) -> dict:
    store = _store(request)
    if any(c.number == channel.number for c in store.config.channels):
        raise HTTPException(status_code=409, detail="Channel number already exists")
    store.config.channels.append(channel)
    store.save()
    return channel.model_dump()


@app.put("/api/channels/{number}")
async def update_channel(number: int, channel: Channel, request: Request) -> dict:
    store = _store(request)
    idx = next(
        (i for i, c in enumerate(store.config.channels) if c.number == number), None
    )
    if idx is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    store.config.channels[idx] = channel
    store.save()
    return channel.model_dump()


@app.delete("/api/channels/{number}")
async def delete_channel(number: int, request: Request) -> dict:
    store = _store(request)
    before = len(store.config.channels)
    store.config.channels = [c for c in store.config.channels if c.number != number]
    if len(store.config.channels) == before:
        raise HTTPException(status_code=404, detail="Channel not found")
    store.save()
    return {"success": True}


# ---- Options, import/export, discovery, status ----


@app.get("/api/options")
async def get_options(request: Request) -> dict:
    return _store(request).config.options.model_dump()


@app.put("/api/options")
async def set_options(options: GlobalOptions, request: Request) -> dict:
    store = _store(request)
    store.config.options = options
    store.save()
    return options.model_dump()


@app.get("/api/export")
async def export_channels(request: Request) -> JSONResponse:
    return JSONResponse(_store(request).export_channels())


@app.post("/api/import")
async def import_channels(request: Request) -> dict:
    body = await request.json()
    data = body.get("channels", body) if isinstance(body, dict) else body
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="Expected a channel list")
    replace = bool(body.get("replace")) if isinstance(body, dict) else False
    count = _store(request).import_channels(data, replace=replace)
    return {"success": True, "imported": count}


@app.get("/api/discover")
async def discover_devices(timeout: float = 5.0) -> list[dict[str, Any]]:
    return await discover(timeout=min(timeout, 15.0))


@app.get("/api/status")
async def status(request: Request) -> dict:
    return {
        "version": __version__,
        "options": _store(request).config.options.model_dump(),
        "tuners": _manager(request).status(),
        "channel_count": len(_store(request).config.channels),
    }


# Static assets (css/js). Mounted last so explicit routes win.
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
