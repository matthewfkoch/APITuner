"""APITuner FastAPI application: dashboard, M3U, streaming, and management API."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .adb_grant import AdbGrantError, grant_agent_permissions
from .agent_update import AgentUpdateError, download_apk, latest_cache
from .backends import BackendNotPaired, BackendUnavailable
from .backends.http_agent import HttpAgentBackend
from .channels import ChannelValidationError, validate_channel_numbers
from .config import ConfigStore
from .diagnostics import build_diagnostics
from .discovery import discover
from .hdhr.discovery import DiscoverIdentity, HdhrDiscoveryService
from .hdhr.lineup import resolve_base_url
from .hdhr.routes import router as hdhr_router
from .log_buffer import install_log_buffer
from .models import Channel, GlobalOptions, Tuner
from .playlist import build_m3u, filter_channels_by_provider
from .stream import open_stream
from .tuner_manager import NoTunerAvailable, TuneFailed, TunerManager

logging.basicConfig(
    level=os.environ.get("APITUNER_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
install_log_buffer()
logger = logging.getLogger("apituner")

WEB_DIR = Path(__file__).parent / "web"
AGENT_APK_RELEASES_URL = os.environ.get(
    "APITUNER_AGENT_APK_URL",
    "https://github.com/matthewfkoch/APITuner-releases/releases",
)


def _http_port() -> int:
    return int(os.environ.get("APITUNER_PORT", "6592"))


def _discovery_identity(store: ConfigStore, manager: TunerManager) -> DiscoverIdentity:
    options = store.config.options
    port = options.hdhr_port or _http_port()
    # Discovery replies use LAN IP filled in by the UDP responder; seed BaseURL
    # with localhost so HTTP clients still get a usable absolute URL shape.
    base = f"http://127.0.0.1:{port}"
    return DiscoverIdentity(
        device_id_hex=options.hdhr_device_id,
        tuner_count=manager.tuner_count(),
        base_url=base,
        friendly_name=options.hdhr_friendly_name,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = ConfigStore()
    manager = TunerManager(store)
    app.state.store = store
    app.state.manager = manager
    app.state.hdhr_discovery = None
    await manager.start_reaper()
    # Best-effort: warm device info so tuner selection is app-aware.
    for tuner in store.config.tuners:
        if tuner.enabled:
            try:
                await manager.refresh_info(tuner.id)
            except Exception:  # noqa: BLE001
                pass

    options = store.config.options
    if options.hdhr_enabled and (
        options.hdhr_ssdp_enabled or options.hdhr_udp_discovery_enabled
    ):
        discovery = HdhrDiscoveryService(
            lambda: _discovery_identity(store, manager),
            ssdp_enabled=options.hdhr_ssdp_enabled,
            udp_enabled=options.hdhr_udp_discovery_enabled,
            http_port=options.hdhr_port or _http_port(),
        )
        app.state.hdhr_discovery = discovery
        try:
            await discovery.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("HDHR discovery failed to start: %s", exc)

    logger.info("APITuner %s started", __version__)
    try:
        yield
    finally:
        discovery = getattr(app.state, "hdhr_discovery", None)
        if discovery is not None:
            await discovery.stop()
        await manager.stop_reaper()


app = FastAPI(title="APITuner", version=__version__, lifespan=lifespan)
app.include_router(hdhr_router)


def _store(request: Request) -> ConfigStore:
    return request.app.state.store


def _manager(request: Request) -> TunerManager:
    return request.app.state.manager


# ---- Dashboard + playlist + streaming ----


@app.get("/", include_in_schema=False)
async def dashboard() -> Response:
    index = WEB_DIR / "index.html"
    if index.exists():
        html = index.read_text(encoding="utf-8").replace(
            "{{AGENT_APK_RELEASES_URL}}", AGENT_APK_RELEASES_URL
        )
        return HTMLResponse(html)
    return PlainTextResponse("APITuner is running. Dashboard assets missing.")


def _channels_playlist(request: Request, provider: str | None = None) -> Response:
    channels = filter_channels_by_provider(
        _store(request).config.channels, provider
    )
    base_url = str(request.base_url)
    return PlainTextResponse(build_m3u(channels, base_url), media_type="audio/x-mpegurl")


@app.get("/channels.m3u", include_in_schema=False)
async def channels_m3u(request: Request, provider: str | None = None) -> Response:
    return _channels_playlist(request, provider)


@app.get("/channels.m3u8", include_in_schema=False)
async def channels_m3u8(request: Request, provider: str | None = None) -> Response:
    # ADBTuner-compatible URL used by Channels DVR custom channel sources.
    return _channels_playlist(request, provider)


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
    manager = _manager(request)
    tuner = next((t for t in _store(request).config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    info = await manager.refresh_info(tuner_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Tuner not found or unreachable")
    backend = manager.get_backend(tuner)
    capabilities: dict[str, bool] = {}
    if hasattr(backend, "get_live_capabilities"):
        try:
            capabilities = await backend.get_live_capabilities()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            capabilities = {}
    if not capabilities:
        static = backend.capabilities
        capabilities = {
            "keys": static.keys,
            "current_app": static.current_app,
            "playback_state": static.playback_state,
            "app_list": static.app_list,
            "install": static.install,
        }
    return {
        "model": info.model,
        "manufacturer": info.manufacturer,
        "os_version": info.os_version,
        "sdk_int": info.sdk_int,
        "packages": info.packages,
        "capabilities": capabilities,
        "version_name": info.agent_version_name,
        "version_code": info.agent_version_code,
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


@app.get("/api/tuners/{tuner_id}/pair/status")
async def pair_status(tuner_id: str, request: Request) -> dict:
    manager = _manager(request)
    tuner = next((t for t in _store(request).config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    backend = manager.get_backend(tuner)
    if not backend.requires_pairing:
        return {"requires_pairing": False, "paired": True}
    try:
        paired = await backend.is_paired()
    except Exception:  # noqa: BLE001
        paired = False
    return {"requires_pairing": True, "paired": paired}


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
    try:
        validate_channel_numbers(store.config.channels)
    except ChannelValidationError as exc:
        store.config.channels.pop()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    if channel.number != number and any(
        c.number == channel.number for c in store.config.channels
    ):
        raise HTTPException(status_code=409, detail="Channel number already exists")
    store.config.channels[idx] = channel
    try:
        validate_channel_numbers(store.config.channels)
    except ChannelValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    # Preserve DeviceID if the client omitted / cleared it.
    if not options.hdhr_device_id:
        options.hdhr_device_id = store.config.options.hdhr_device_id
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
    # Nested wrap: { "channels": { "channels": [...] } } from a paste + UI envelope.
    if isinstance(data, dict) and isinstance(data.get("channels"), list):
        data = data["channels"]
    if not isinstance(data, list):
        raise HTTPException(
            status_code=400,
            detail="Expected a channel list (JSON array, or an object with a channels array)",
        )
    replace = bool(body.get("replace")) if isinstance(body, dict) else False
    try:
        count = _store(request).import_channels(data, replace=replace)
    except ChannelValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "imported": count}


@app.get("/api/discover")
async def discover_devices(timeout: float = 5.0) -> list[dict[str, Any]]:
    return await discover(timeout=min(timeout, 15.0))


@app.get("/api/agent/latest")
async def agent_latest(force: bool = False) -> dict[str, Any]:
    """Return the cached public Agent APK latest.json manifest."""
    try:
        latest = await latest_cache.get(force=force)
    except AgentUpdateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return latest.to_dict()


@app.post("/api/tuners/{tuner_id}/grant-permissions")
async def grant_tuner_permissions(tuner_id: str, request: Request) -> dict:
    """One-time Agent permission grant via network ADB (Fire TV setup).

    Day-to-day tuning stays on the Agent HTTP API. Fire OS hides special-access
    Settings toggles for sideloaded apps; network ADB is only used here for setup.
    """
    store = _store(request)
    tuner = next((t for t in store.config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    if tuner.control.type != "http_agent":
        raise HTTPException(
            status_code=400,
            detail="Permission grant via ADB is only for http_agent tuners",
        )
    try:
        result = await grant_agent_permissions(tuner.control.host)
    except AdbGrantError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await _manager(request).refresh_info(tuner_id)
    payload = result.to_dict()
    payload["message"] = (
        "Granted overlay, usage, and notification access"
        if payload["success"]
        else "Partial grant — check messages"
    )
    if not result.accessibility:
        payload["message"] += (
            ". If HOME/BACK keys are needed, also enable APITuner Agent under "
            "Settings → Accessibility on the device (Fire may require a one-time confirm)."
        )
    return payload


@app.post("/api/tuners/{tuner_id}/update-agent")
async def update_agent(tuner_id: str, request: Request) -> dict[str, Any]:
    """Download the latest Agent APK and push it to an http_agent device."""
    store = _store(request)
    manager = _manager(request)
    tuner = next((t for t in store.config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    if tuner.control.type != "http_agent":
        raise HTTPException(
            status_code=400,
            detail="Agent updates are only supported for http_agent tuners",
        )

    backend = manager.get_backend(tuner)
    if not isinstance(backend, HttpAgentBackend):
        raise HTTPException(status_code=400, detail="Backend is not http_agent")

    try:
        latest = await latest_cache.get(force=True)
    except AgentUpdateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    info = await manager.refresh_info(tuner_id)
    current_code = info.agent_version_code if info else None
    if current_code is not None and current_code >= latest.version_code:
        return {
            "success": True,
            "updated": False,
            "message": "Agent already up to date",
            "version_name": info.agent_version_name if info else None,
            "version_code": current_code,
            "latest": latest.to_dict(),
        }

    data_dir = store.data_dir
    cache_dir = Path(data_dir) / "agent-apk-cache"
    try:
        apk_path = await download_apk(latest, cache_dir)
        result = await backend.upload_apk(apk_path)
    except AgentUpdateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except BackendUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "success": True,
        "updated": True,
        "message": result.get("message")
        or "Install dialog opened on the TV — confirm with the remote",
        "latest": latest.to_dict(),
        "previous_version_code": current_code,
    }


@app.get("/api/status")
async def status(request: Request) -> dict:
    store = _store(request)
    manager = _manager(request)
    options = store.config.options
    base = resolve_base_url(str(request.base_url), options)
    xmltv_url = f"{base}/xmltv.xml" if options.hdhr_enabled else None
    return {
        "version": __version__,
        "agent_apk_url": AGENT_APK_RELEASES_URL,
        "agent_latest_url": latest_cache.url,
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
            "xmltv_url": xmltv_url if options.channels_dvr_url else None,
            "channels_dvr_url": options.channels_dvr_url,
            "ssdp_enabled": options.hdhr_ssdp_enabled,
            "udp_discovery_enabled": options.hdhr_udp_discovery_enabled,
            "discovery_running": getattr(request.app.state, "hdhr_discovery", None)
            is not None,
        },
    }


@app.get("/api/diagnostics")
async def diagnostics(request: Request) -> Response:
    """Downloadable support bundle (tokens redacted; may include LAN IPs)."""
    import json
    from datetime import datetime, timezone

    bundle = await build_diagnostics(_store(request), _manager(request))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    body = json.dumps(bundle, indent=2, sort_keys=False)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="apituner-diagnostics-{stamp}.json"'
        },
    )


# Static assets (css/js). Mounted last so explicit routes win.
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
