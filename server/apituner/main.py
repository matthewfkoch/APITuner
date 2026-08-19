"""APITuner FastAPI application: dashboard, M3U, streaming, and management API."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .adb_grant import AdbGrantError, grant_agent_permissions
from .agent_update import AgentUpdateError, download_apk, latest_cache
from .auto_pair import PairKind, auto_pair
from .backends import BackendNotPaired, BackendUnavailable
from .backends.http_agent import HttpAgentBackend
from .channels import ChannelValidationError, validate_channel_numbers
from .config import ConfigStore
from .deeplink_catalog import catalog_payload
from .diagnostics import build_diagnostics
from .discovery import discover
from .fruitdeeplinks import FruitDeepLinksError, sync_fruitdeeplinks
from .hdhr.discovery import DiscoverIdentity, HdhrDiscoveryService
from .hdhr.lineup import resolve_base_url
from .hdhr.routes import router as hdhr_router
from .log_buffer import install_log_buffer
from .m3u_import import channels_from_m3u
from .models import Channel, GlobalOptions, Tuner
from .keys import key_requires_dpad
from .package_coverage import build_package_coverage
from .playlist import build_m3u, filter_channels_by_provider
from .preview import grab_preview_jpeg, have_ffmpeg, jpeg_response, mjpeg_response
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
    sync_task = asyncio.create_task(_fruitdeeplinks_sync_loop(store))
    app.state.fdl_sync_task = sync_task
    try:
        yield
    finally:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass
        discovery = getattr(app.state, "hdhr_discovery", None)
        if discovery is not None:
            await discovery.stop()
        await manager.stop_reaper()


app = FastAPI(title="APITuner", version=__version__, lifespan=lifespan)
app.include_router(hdhr_router)


async def _fruitdeeplinks_sync_loop(store: ConfigStore) -> None:
    """Optional background refresh of FruitDeepLinks ADB lanes."""
    while True:
        options = store.config.options
        seconds = float(options.fruitdeeplinks_sync_seconds or 0)
        url = (options.fruitdeeplinks_url or "").strip()
        if seconds > 0 and url:
            try:
                await sync_fruitdeeplinks(store)
            except Exception:  # noqa: BLE001
                logger.exception("FruitDeepLinks background sync failed")
            await asyncio.sleep(max(30.0, seconds))
        else:
            await asyncio.sleep(30.0)


def _store(request: Request) -> ConfigStore:
    return request.app.state.store


def _manager(request: Request) -> TunerManager:
    return request.app.state.manager


# ---- Dashboard + playlist + streaming ----


@app.get("/", include_in_schema=False)
async def dashboard() -> Response:
    index = WEB_DIR / "index.html"
    if index.exists():
        html = (
            index.read_text(encoding="utf-8")
            .replace("{{AGENT_APK_RELEASES_URL}}", AGENT_APK_RELEASES_URL)
            .replace("{{VERSION}}", __version__)
        )
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-store"},
        )
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


@app.get("/api/package-coverage")
async def package_coverage(request: Request) -> dict:
    """Installed apps on Agent/ADB tuners vs channel package_name fields.

    Used by the dashboard to warn when a channel package is not installed
    (e.g. com.espn.gtv on a Fire stick that only has com.espn.score_center).
    """
    store = _store(request)
    return await build_package_coverage(
        store.config.tuners,
        store.config.channels,
        _manager(request),
    )


@app.get("/api/tuners/{tuner_id}/preview.jpg", include_in_schema=False)
async def tuner_preview_jpeg(tuner_id: str, request: Request) -> Response:
    """Single-frame JPEG of the tuner's HDMI encoder stream."""
    tuner = next((t for t in _store(request).config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    if not have_ffmpeg():
        raise HTTPException(
            status_code=503,
            detail="ffmpeg not available (install ffmpeg or use the Docker image)",
        )
    data = await grab_preview_jpeg(tuner.stream_endpoint)
    if not data:
        raise HTTPException(
            status_code=504,
            detail="Could not grab a frame from the encoder stream",
        )
    return jpeg_response(data)


@app.get("/api/tuners/{tuner_id}/preview.mjpg", include_in_schema=False)
@app.get("/api/tuners/{tuner_id}/preview", include_in_schema=False)
async def tuner_preview_mjpeg(tuner_id: str, request: Request) -> Response:
    """Live MJPEG (multipart) preview of the HDMI encoder — dashboard use."""
    tuner = next((t for t in _store(request).config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    if not (tuner.stream_endpoint or "").strip():
        raise HTTPException(status_code=400, detail="Tuner has no stream_endpoint")
    if not have_ffmpeg():
        raise HTTPException(
            status_code=503,
            detail="ffmpeg not available (install ffmpeg or use the Docker image)",
        )
    return mjpeg_response(tuner.stream_endpoint, request)


@app.post("/api/tuners/{tuner_id}/key")
async def tuner_send_key(tuner_id: str, request: Request) -> dict:
    """Send a remote key / power action for dashboard preview controls."""
    tuner = next((t for t in _store(request).config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    key = str((body or {}).get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="key is required")

    manager = _manager(request)
    backend = manager.get_command_backend(tuner)
    name = key.upper().replace("KEYCODE_", "")

    # Fail fast with a setup hint when D-pad is requested without a keys plane.
    caps = backend.capabilities
    if key_requires_dpad(name) and not caps.dpad:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Key {name} needs a D-pad backend. Edit this tuner → set "
                "Keys / D-pad to androidtv_remote (Google TV / onn), firetv_rest, "
                "or adb, then Pair. Agent alone only supports Back / Home."
            ),
        )

    try:
        if name == "REBOOT":
            if getattr(backend.capabilities, "shell", False) and hasattr(
                backend, "run_shell"
            ):
                await backend.run_shell("reboot")  # type: ignore[attr-defined]
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Reboot needs an adb keys backend on this tuner",
                )
        elif name in ("WAKE", "POWER_ON"):
            # Prefer explicit wake helper (Fire REST); else POWER keyevent.
            wake = getattr(backend, "wake", None)
            if callable(wake):
                await wake()
            else:
                await backend.send_key("POWER")
        elif name in ("SLEEP", "POWER_OFF", "POWER"):
            await backend.send_key("SLEEP" if name == "SLEEP" else "POWER")
        else:
            await backend.send_key(name)
    except HTTPException:
        raise
    except BackendNotPaired as exp:
        raise HTTPException(
            status_code=400,
            detail=f"{exp}. Pair the Keys / D-pad backend for this tuner first.",
        ) from exp
    except BackendUnavailable as exp:
        raise HTTPException(status_code=502, detail=str(exp)) from exp
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Key failed: {exc}") from exc
    return {"success": True, "key": name}


# ---- Pairing (androidtv_remote / firetv_rest backends) ----


@app.get("/api/tuners/{tuner_id}/pair/status")
async def pair_status(tuner_id: str, request: Request) -> dict:
    manager = _manager(request)
    tuner = next((t for t in _store(request).config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    backend = manager.get_pairing_backend(tuner)
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
    backend = manager.get_pairing_backend(tuner)
    if not backend.requires_pairing:
        raise HTTPException(status_code=400, detail="Backend does not require pairing")
    try:
        await backend.start_pairing()
    except Exception as exc:  # noqa: BLE001
        detail = str(exc).strip() or type(exc).__name__
        raise HTTPException(
            status_code=502,
            detail=(
                f"Pairing failed: {detail}. "
                "Cancel any pairing dialog on the TV, then try again."
            ),
        ) from exc
    return {"success": True, "message": "Enter the PIN shown on the TV"}


@app.post("/api/tuners/{tuner_id}/pair/finish")
async def pair_finish(tuner_id: str, request: Request) -> dict:
    manager = _manager(request)
    store = _store(request)
    tuner = next((t for t in store.config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    body = await request.json()
    pin = str(body.get("pin", "")).strip()
    if not pin:
        raise HTTPException(status_code=400, detail="Missing pin")
    backend = manager.get_pairing_backend(tuner)
    try:
        await backend.finish_pairing(pin)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Pairing failed: {exc}") from exc
    _persist_fire_pair_token(store, tuner, backend)
    await manager.refresh_info(tuner_id)
    return {"success": True, "message": "Paired successfully"}


def _pairing_kind(tuner: Tuner, backend) -> PairKind | None:
    """Resolve androidtv_remote vs firetv_rest for PIN OCR shape."""
    for cfg in (tuner.keys_control, tuner.control):
        if cfg is None:
            continue
        if cfg.type in ("androidtv_remote", "firetv_rest"):
            return cfg.type  # type: ignore[return-value]
    name = type(backend).__name__.lower()
    if "fire" in name:
        return "firetv_rest"
    if "android" in name or "remote" in name:
        return "androidtv_remote"
    return None


def _persist_fire_pair_token(store: ConfigStore, tuner: Tuner, backend) -> None:
    """Persist Fire TV REST client token onto the keys or primary control config."""
    token = getattr(backend, "client_token", None)
    if not token:
        return
    if tuner.keys_control and tuner.keys_control.type == "firetv_rest":
        tuner.keys_control.token = token
        store.save()
    elif tuner.control.type == "firetv_rest":
        tuner.control.token = token
        store.save()


@app.post("/api/tuners/{tuner_id}/pair/auto")
async def pair_auto(tuner_id: str, request: Request) -> dict:
    """Start pairing (unless already started), OCR the PIN, and finish pairing."""
    manager = _manager(request)
    store = _store(request)
    tuner = next((t for t in store.config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    backend = manager.get_pairing_backend(tuner)
    if not backend.requires_pairing:
        raise HTTPException(status_code=400, detail="Backend does not require pairing")
    kind = _pairing_kind(tuner, backend)
    if kind is None:
        raise HTTPException(
            status_code=400,
            detail="Auto-pair only supports androidtv_remote and firetv_rest",
        )
    stream = (tuner.stream_endpoint or "").strip()
    if not stream:
        raise HTTPException(
            status_code=400,
            detail=(
                "Auto-pair needs a stream endpoint (HDMI encoder URL) "
                "to read the PIN from the TV"
            ),
        )
    already_started = False
    try:
        body = await request.json()
        if isinstance(body, dict):
            already_started = bool(body.get("already_started"))
    except Exception:  # noqa: BLE001 - empty body is fine
        pass
    result = await auto_pair(
        backend, stream, kind=kind, already_started=already_started
    )
    if result.success:
        _persist_fire_pair_token(store, tuner, backend)
        await manager.refresh_info(tuner_id)
    return result.as_dict()


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


# ---- Tune configurations (babsonnexus / ADBTuner App Play) ----


@app.get("/api/configurations")
async def list_configurations(request: Request) -> list[dict]:
    return _store(request).export_configurations()


@app.post("/api/configurations/import")
async def import_configurations(request: Request) -> dict:
    body = await request.json()
    data = body.get("configurations", body) if isinstance(body, dict) else body
    if isinstance(data, dict) and isinstance(data.get("configurations"), list):
        data = data["configurations"]
    replace = bool(body.get("replace")) if isinstance(body, dict) else False
    try:
        count = _store(request).import_configurations(data, replace=replace)
    except ChannelValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "imported": count}


@app.get("/api/configurations/export")
async def export_configurations(request: Request) -> JSONResponse:
    return JSONResponse(_store(request).export_configurations())


@app.delete("/api/configurations/{uuid}")
async def delete_configuration(uuid: str, request: Request) -> dict:
    store = _store(request)
    before = len(store.config.configurations)
    store.config.configurations = [
        c for c in store.config.configurations if c.uuid != uuid
    ]
    if len(store.config.configurations) == before:
        raise HTTPException(status_code=404, detail="Configuration not found")
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


@app.get("/api/deeplink-catalog")
async def deeplink_catalog() -> dict:
    """Provider / scheme → Android package map for FruitDeepLinks and similar sources."""
    return catalog_payload()


@app.post("/api/fruitdeeplinks/sync")
async def fruitdeeplinks_sync(request: Request) -> dict:
    try:
        return await sync_fruitdeeplinks(_store(request))
    except FruitDeepLinksError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/import")
async def import_channels(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict) and not isinstance(body, list):
        raise HTTPException(status_code=400, detail="Expected JSON object or array")

    replace = bool(body.get("replace")) if isinstance(body, dict) else False
    profile = None
    start_number = 9000
    if isinstance(body, dict):
        profile = body.get("profile")
        try:
            start_number = int(body.get("start_number") or 9000)
        except (TypeError, ValueError):
            start_number = 9000

    skipped: list[dict[str, str]] = []
    data: Any = None

    if isinstance(body, list):
        data = body
    elif isinstance(body, dict) and isinstance(body.get("m3u"), str):
        data, skipped = channels_from_m3u(
            body["m3u"], profile=profile, start_number=start_number
        )
    elif isinstance(body, dict) and body.get("url"):
        fetch_url = str(body["url"]).strip()
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(fetch_url)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502, detail=f"Failed to fetch playlist: {exc}"
            ) from exc
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"Playlist URL returned HTTP {resp.status_code}",
            )
        text = resp.text or ""
        stripped = text.lstrip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                parsed = resp.json()
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=400, detail=f"Playlist JSON is invalid: {exc}"
                ) from exc
            data = parsed.get("channels", parsed) if isinstance(parsed, dict) else parsed
        else:
            data, skipped = channels_from_m3u(
                text, profile=profile, start_number=start_number
            )
    else:
        data = body.get("channels", body) if isinstance(body, dict) else body
        if isinstance(data, dict) and isinstance(data.get("channels"), list):
            data = data["channels"]

    if not isinstance(data, list):
        raise HTTPException(
            status_code=400,
            detail=(
                "Expected a channel list (JSON array), M3U text in 'm3u', "
                "or a playlist 'url'"
            ),
        )
    try:
        count = _store(request).import_channels(data, replace=replace)
    except ChannelValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result: dict[str, Any] = {"success": True, "imported": count}
    if skipped:
        result["skipped"] = skipped
    return result

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
    if tuner.control.type not in ("http_agent", "adb"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Permission grant via ADB is for http_agent or adb tuners "
                "(needs network ADB to the device)"
            ),
        )
    host = (tuner.control.host or "").split(":", 1)[0].strip()
    if not host:
        raise HTTPException(status_code=400, detail="Tuner host is empty")
    # http_agent uses :9092; network ADB grant always targets the ADB port.
    if tuner.control.type == "adb":
        adb_port = tuner.control.port or 5555
    else:
        adb_port = 5555
    try:
        result = await grant_agent_permissions(host, adb_port=adb_port)
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
            ". Accessibility / Send keys did not bind — re-run Grant permissions (ADB); "
            "Fire OS often needs the one-time reboot that grant performs (no on-device toggle)."
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
    epg_source = bool(
        (options.channels_dvr_url or "").strip()
        or (options.fruitdeeplinks_url or "").strip()
    )
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
            "xmltv_url": xmltv_url,
            "epg_source": epg_source,
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
