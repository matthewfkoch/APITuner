"""Channel, tune configuration, global option, import/export, and catalog sync routes."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr

from ..channels import ChannelValidationError
from ..deeplink_catalog import catalog_payload
from ..fastchannels import FastChannelsError, connection_url, sync_fastchannels
from ..fruitdeeplinks import FruitDeepLinksError, sync_fruitdeeplinks
from ..m3u_import import channels_from_m3u
from ..models import Channel, GlobalOptions
from . import get_store

MAX_IMPORT_BYTES = 16 * 1024 * 1024

router = APIRouter()


class FastChannelsConnection(BaseModel):
    url: str
    credential: SecretStr = SecretStr("")


# ---- Channel management ----


@router.get("/api/channels")
async def list_channels(request: Request) -> list[dict]:
    return [c.model_dump() for c in get_store(request).config.channels]


@router.post("/api/channels")
async def create_channel(channel: Channel, request: Request) -> dict:
    from .. import main as app_main

    store = get_store(request)
    with store._lock:
        if any(c.id == channel.id for c in store.config.channels):
            raise HTTPException(status_code=409, detail="Channel id already exists")
        store.config.channels.append(channel)
        try:
            app_main.validate_unique_ids(store.config.channels)
        except ChannelValidationError as exc:
            store.config.channels.pop()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.save()
        return store.config.channels[-1].model_dump()


@router.put("/api/channels/{channel_id}")
async def update_channel(channel_id: str, channel: Channel, request: Request) -> dict:
    from .. import main as app_main

    store = get_store(request)
    with store._lock:
        idx = next(
            (i for i, c in enumerate(store.config.channels) if c.id == channel_id), None
        )
        if idx is None:
            raise HTTPException(status_code=404, detail="Channel not found")
        previous = store.config.channels[idx]
        updates: dict = {"id": channel_id}
        if not (channel.source or "").strip() and previous.source:
            updates["source"] = previous.source
        if previous.source == "fastchannels":
            for field in (
                "source",
                "available",
                "fastchannels_source",
                "fastchannels_channel_id",
            ):
                updates[field] = getattr(previous, field)
        updated = channel.model_copy(update=updates)
        store.config.channels[idx] = updated
        try:
            app_main.validate_unique_ids(store.config.channels)
        except ChannelValidationError as exc:
            store.config.channels[idx] = previous
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.save()
        return store.config.channels[idx].model_dump()


@router.delete("/api/channels/{channel_id}")
async def delete_channel(channel_id: str, request: Request) -> dict:
    store = get_store(request)
    with store._lock:
        before = len(store.config.channels)
        store.config.channels = [
            c for c in store.config.channels if c.id != channel_id
        ]
        if len(store.config.channels) == before:
            raise HTTPException(status_code=404, detail="Channel not found")
        store.save()
    return {"success": True}


# ---- Tune configurations (babsonnexus / ADBTuner App Play) ----


@router.get("/api/configurations")
async def list_configurations(request: Request) -> list[dict]:
    return get_store(request).export_configurations()


@router.post("/api/configurations/import")
async def import_configurations(request: Request) -> dict:
    body = await request.json()
    data = body.get("configurations", body) if isinstance(body, dict) else body
    if isinstance(data, dict) and isinstance(data.get("configurations"), list):
        data = data["configurations"]
    replace = bool(body.get("replace")) if isinstance(body, dict) else False
    try:
        count = get_store(request).import_configurations(data, replace=replace)
    except ChannelValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "imported": count}


@router.get("/api/configurations/export")
async def export_configurations(request: Request) -> JSONResponse:
    return JSONResponse(get_store(request).export_configurations())


@router.delete("/api/configurations/{uuid}")
async def delete_configuration(uuid: str, request: Request) -> dict:
    store = get_store(request)
    with store._lock:
        before = len(store.config.configurations)
        store.config.configurations = [
            c for c in store.config.configurations if c.uuid != uuid
        ]
        if len(store.config.configurations) == before:
            raise HTTPException(status_code=404, detail="Configuration not found")
        store.save()
    return {"success": True}


# ---- Options, import/export, catalog sync ----


@router.get("/api/options")
async def get_options(request: Request) -> dict:
    return get_store(request).config.options.model_dump()


@router.put("/api/options")
async def set_options(options: GlobalOptions, request: Request) -> dict:
    store = get_store(request)
    with store._lock:
        previous = store.config.options
        if not options.hdhr_device_id:
            options.hdhr_device_id = previous.hdhr_device_id
        if "fastchannels_url" not in options.model_fields_set:
            options.fastchannels_url = previous.fastchannels_url
        options.fastchannels_token = (
            previous.fastchannels_token
            if connection_url(options.fastchannels_url)
            == connection_url(previous.fastchannels_url)
            else None
        )
        store.config.options = options
        store.save()
    return options.model_dump()


@router.get("/api/export")
async def export_channels(request: Request, native: int = 0) -> JSONResponse:
    return JSONResponse(get_store(request).export_channels(native=bool(native)))


@router.get("/api/deeplink-catalog")
async def deeplink_catalog() -> dict:
    """Provider / scheme → Android package map for FruitDeepLinks and similar sources."""
    return catalog_payload()


@router.post("/api/fastchannels/sync")
async def fastchannels_sync(request: Request) -> dict:
    try:
        return await sync_fastchannels(get_store(request))
    except FastChannelsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/api/fastchannels/connect")
async def fastchannels_connect(
    connection: FastChannelsConnection, request: Request
) -> dict:
    credential = connection.credential.get_secret_value().strip()
    if len(credential) > 1024 or any(not 33 <= ord(ch) <= 126 for ch in credential):
        raise HTTPException(
            status_code=400, detail="Enter a valid FastChannels external player credential."
        )
    try:
        return await sync_fastchannels(
            get_store(request), url=connection.url, credential=credential
        )
    except (FastChannelsError, ChannelValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/api/fruitdeeplinks/sync")
async def fruitdeeplinks_sync(request: Request) -> dict:
    try:
        return await sync_fruitdeeplinks(get_store(request))
    except FruitDeepLinksError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/import")
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
        parsed_url = urlsplit(fetch_url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname:
            raise HTTPException(
                status_code=400, detail="Playlist URL must use http:// or https://"
            )
        buf = bytearray()
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                async with client.stream("GET", fetch_url) as resp:
                    if resp.status_code >= 400:
                        raise HTTPException(
                            status_code=502,
                            detail=f"Playlist URL returned HTTP {resp.status_code}",
                        )
                    async for chunk in resp.aiter_bytes(64 * 1024):
                        buf.extend(chunk)
                        if len(buf) > MAX_IMPORT_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail=f"Playlist URL exceeds {MAX_IMPORT_BYTES // (1024 * 1024)} MiB limit",
                            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502, detail=f"Failed to fetch playlist: {exc}"
            ) from exc
        text = buf.decode("utf-8", errors="replace")
        stripped = text.lstrip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                parsed = json.loads(text)
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
        count = get_store(request).import_channels(data, replace=replace)
    except ChannelValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result: dict[str, Any] = {"success": True, "imported": count}
    if skipped:
        result["skipped"] = skipped
    return result
