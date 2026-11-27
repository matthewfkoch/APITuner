"""Tuner management, preview, remote keys, pairing, ADB grant, and Agent update routes."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..adb_grant import AdbGrantError
from ..agent_update import AgentUpdateError
from ..auto_pair import PairKind
from ..backends import BackendNotPaired, BackendUnavailable
from ..backends.http_agent import HttpAgentBackend
from ..config import ConfigStore
from ..keys import key_requires_dpad
from ..models import Tuner
from ..package_coverage import build_package_coverage
from ..preview import grab_preview_jpeg, have_ffmpeg, jpeg_response, mjpeg_response
from . import get_manager, get_store

logger = logging.getLogger("apituner")

router = APIRouter()


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
    with store._lock:
        if tuner.keys_control and tuner.keys_control.type == "firetv_rest":
            tuner.keys_control.token = token
            store.save()
        elif tuner.control.type == "firetv_rest":
            tuner.control.token = token
            store.save()


def _required_permissions(perms: dict[str, bool] | None) -> bool:
    if not perms:
        return False
    return bool(perms.get("overlay") and perms.get("usage") and perms.get("notification"))


def _already_granted_payload(
    perms: dict[str, bool],
    *,
    adb_detail: str | None = None,
    skipped_adb: bool = True,
) -> dict[str, Any]:
    accessibility = bool(perms.get("accessibility"))
    message = "Agent already has overlay, usage, and notification access."
    if accessibility:
        message += " Accessibility is enabled too."
    elif adb_detail:
        message += (
            " Accessibility is still off (only needed for BACK/HOME/RECENTS). "
            "On Google TV enable it in the Agent app. On Fire TV, network ADB "
            f"port 5555 has to be reachable to bind it. ADB: {adb_detail}"
        )
    else:
        message += (
            " Accessibility is still off (only needed for BACK/HOME/RECENTS). "
            "On Google TV enable it in the Agent app."
        )
    return {
        "success": True,
        "overlay": True,
        "usage": True,
        "notification": True,
        "accessibility": accessibility,
        "skipped_adb": skipped_adb,
        "message": message,
    }


async def _agent_permissions(
    request: Request, tuner: Tuner
) -> dict[str, bool] | None:
    if tuner.control.type != "http_agent":
        return None
    backend = get_manager(request).get_backend(tuner)
    if not isinstance(backend, HttpAgentBackend):
        return None
    try:
        return await backend.agent_permissions()
    except Exception:  # noqa: BLE001
        logger.debug("Could not read Agent permissions for %s", tuner.name, exc_info=True)
        return None


@router.get("/api/tuners")
async def list_tuners(request: Request) -> list[dict]:
    return [t.model_dump() for t in get_store(request).config.tuners]


@router.post("/api/tuners")
async def create_tuner(tuner: Tuner, request: Request) -> dict:
    store = get_store(request)
    with store._lock:
        if any(t.id == tuner.id for t in store.config.tuners):
            raise HTTPException(status_code=409, detail="Tuner id already exists")
        store.config.tuners.append(tuner)
        store.save()
        return store.config.tuners[-1].model_dump()


@router.put("/api/tuners/{tuner_id}")
async def update_tuner(tuner_id: str, tuner: Tuner, request: Request) -> dict:
    store = get_store(request)
    manager = get_manager(request)
    with store._lock:
        idx = next(
            (i for i, t in enumerate(store.config.tuners) if t.id == tuner_id), None
        )
        if idx is None:
            raise HTTPException(status_code=404, detail="Tuner not found")
        updated = tuner.model_copy(update={"id": tuner_id})
        store.config.tuners[idx] = updated
        store.save()
        payload = store.config.tuners[idx].model_dump()
    await manager.invalidate(tuner_id)
    return payload


@router.delete("/api/tuners/{tuner_id}")
async def delete_tuner(tuner_id: str, request: Request) -> dict:
    store = get_store(request)
    manager = get_manager(request)
    with store._lock:
        before = len(store.config.tuners)
        store.config.tuners = [t for t in store.config.tuners if t.id != tuner_id]
        if len(store.config.tuners) == before:
            raise HTTPException(status_code=404, detail="Tuner not found")
        store.save()
    await manager.invalidate(tuner_id)
    return {"success": True}


@router.get("/api/tuners/{tuner_id}/health")
async def tuner_health(tuner_id: str, request: Request) -> dict:
    return {"online": await get_manager(request).health(tuner_id)}


@router.get("/api/tuners/{tuner_id}/info")
async def tuner_info(tuner_id: str, request: Request) -> dict:
    manager = get_manager(request)
    tuner = next((t for t in get_store(request).config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    info = await manager.refresh_info(tuner_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Tuner not found or unreachable")
    backend = manager.get_command_backend(tuner)
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


@router.get("/api/tuners/{tuner_id}/apps")
async def tuner_apps(tuner_id: str, request: Request) -> list[dict]:
    manager = get_manager(request)
    tuner = next((t for t in get_store(request).config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    backend = manager.get_backend(tuner)
    if hasattr(backend, "list_apps"):
        try:
            return await backend.list_apps()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    info = await manager.refresh_info(tuner_id)
    if info is None:
        return []
    return [{"name": p, "packageName": p} for p in info.packages]


@router.get("/api/package-coverage")
async def package_coverage(request: Request) -> dict:
    """Installed apps on Agent/ADB tuners vs channel package_name fields."""
    store = get_store(request)
    return await build_package_coverage(
        store.config.tuners,
        store.config.channels,
        get_manager(request),
    )


@router.get("/api/tuners/{tuner_id}/preview.jpg", include_in_schema=False)
async def tuner_preview_jpeg(tuner_id: str, request: Request) -> Response:
    """Single-frame JPEG of the tuner's HDMI encoder stream."""
    tuner = next((t for t in get_store(request).config.tuners if t.id == tuner_id), None)
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


@router.get("/api/tuners/{tuner_id}/preview.mjpg", include_in_schema=False)
@router.get("/api/tuners/{tuner_id}/preview", include_in_schema=False)
async def tuner_preview_mjpeg(tuner_id: str, request: Request) -> Response:
    """Live MJPEG (multipart) preview of the HDMI encoder — dashboard use."""
    tuner = next((t for t in get_store(request).config.tuners if t.id == tuner_id), None)
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


@router.post("/api/tuners/{tuner_id}/key")
async def tuner_send_key(tuner_id: str, request: Request) -> dict:
    """Send a remote key / power action for dashboard preview controls."""
    tuner = next((t for t in get_store(request).config.tuners if t.id == tuner_id), None)
    if tuner is None:
        raise HTTPException(status_code=404, detail="Tuner not found")
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    key = str((body or {}).get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="key is required")

    manager = get_manager(request)
    backend = manager.get_command_backend(tuner)
    name = key.upper().replace("KEYCODE_", "")

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


@router.get("/api/tuners/{tuner_id}/pair/status")
async def pair_status(tuner_id: str, request: Request) -> dict:
    manager = get_manager(request)
    tuner = next((t for t in get_store(request).config.tuners if t.id == tuner_id), None)
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


@router.post("/api/tuners/{tuner_id}/pair/start")
async def pair_start(tuner_id: str, request: Request) -> dict:
    manager = get_manager(request)
    tuner = next((t for t in get_store(request).config.tuners if t.id == tuner_id), None)
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


@router.post("/api/tuners/{tuner_id}/pair/finish")
async def pair_finish(tuner_id: str, request: Request) -> dict:
    manager = get_manager(request)
    store = get_store(request)
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


@router.post("/api/tuners/{tuner_id}/pair/auto")
async def pair_auto(tuner_id: str, request: Request) -> dict:
    """Start pairing (unless already started), OCR the PIN, and finish pairing."""
    from .. import main as app_main

    manager = get_manager(request)
    store = get_store(request)
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
    result = await app_main.auto_pair(
        backend, stream, kind=kind, already_started=already_started
    )
    if result.success:
        _persist_fire_pair_token(store, tuner, backend)
        await manager.refresh_info(tuner_id)
    return result.as_dict()


@router.post("/api/tuners/{tuner_id}/grant-permissions")
async def grant_tuner_permissions(tuner_id: str, request: Request) -> dict:
    """One-time Agent permission grant via network ADB (Fire TV setup)."""
    from .. import main as app_main

    store = get_store(request)
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
    adb_port = (tuner.control.port or 5555) if tuner.control.type == "adb" else 5555

    already = await _agent_permissions(request, tuner)
    if (
        already is not None
        and _required_permissions(already)
        and already.get("accessibility")
    ):
        return _already_granted_payload(already)

    try:
        result = await app_main.grant_agent_permissions(host, adb_port=adb_port)
    except AdbGrantError as exc:
        if already is not None and _required_permissions(already):
            return _already_granted_payload(
                already, adb_detail=str(exc), skipped_adb=False
            )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Permission grant failed for %s", tuner_id)
        if already is not None and _required_permissions(already):
            return _already_granted_payload(
                already, adb_detail=str(exc), skipped_adb=False
            )
        raise HTTPException(
            status_code=502, detail=f"Permission grant failed: {exc}"
        ) from exc
    await get_manager(request).refresh_info(tuner_id)
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


@router.post("/api/tuners/{tuner_id}/update-agent")
async def update_agent(tuner_id: str, request: Request) -> dict[str, Any]:
    """Download the latest Agent APK and push it to an http_agent device."""
    from .. import main as app_main

    store = get_store(request)
    manager = get_manager(request)
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
        latest = await app_main.latest_cache.get(force=True)
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

    cache_dir = Path(store.data_dir) / "agent-apk-cache"
    try:
        apk_path = await app_main.download_apk(latest, cache_dir)
        result = await backend.upload_apk(apk_path)
    except (AgentUpdateError, BackendUnavailable) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "success": True,
        "updated": True,
        "message": result.get("message")
        or "Install dialog opened on the TV — confirm with the remote",
        "latest": latest.to_dict(),
        "previous_version_code": current_code,
    }
