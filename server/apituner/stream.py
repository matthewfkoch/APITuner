"""Encoder MPEG-TS relay (proxy) and redirect stream handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from .tuner_manager import Lease, TunerManager

logger = logging.getLogger(__name__)

_CHUNK = 64 * 1024


async def _delayed_release(manager: TunerManager, lease: Lease, grace: float) -> None:
    await asyncio.sleep(max(0.0, grace))
    await manager.release(lease)


async def _proxy_iter(
    request: Request, manager: TunerManager, lease: Lease
) -> AsyncIterator[bytes]:
    """Relay the encoder's MPEG-TS to the client, tracking bytes for lifecycle."""
    url = lease.tuner.stream_endpoint
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=15.0))
    try:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(_CHUNK):
                if await request.is_disconnected():
                    break
                manager.touch(lease.tune_id, len(chunk))
                yield chunk
    except (httpx.HTTPError, asyncio.CancelledError, GeneratorExit) as exc:
        logger.info("Stream %s ended: %s", lease.tune_id, type(exc).__name__)
    finally:
        await client.aclose()
        # Release after a short grace, matching ADBTuner's ~5s unlock delay.
        asyncio.create_task(
            _delayed_release(manager, lease, manager.options.release_grace_seconds)
        )


async def open_stream(request: Request, manager: TunerManager, channel) -> Response:
    """Acquire a tuner, tune the channel, and return a stream response."""
    lease = await manager.lease(channel)

    if manager.options.stream_mode == "redirect":
        # Video flows encoder -> Channels directly; reclaim the tuner after an
        # idle window since we can't observe the byte stream.
        asyncio.create_task(
            _delayed_release(manager, lease, manager.options.tuner_idle_timeout_seconds)
        )
        return RedirectResponse(lease.tuner.stream_endpoint, status_code=302)

    return StreamingResponse(
        _proxy_iter(request, manager, lease),
        media_type="video/mp2t",
    )
