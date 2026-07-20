"""Encoder MPEG-TS relay (proxy) and redirect stream handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from .tuner_manager import Lease, TunerManager

logger = logging.getLogger(__name__)

_CHUNK = 64 * 1024
HDHR_SERVER_HEADER = "HDHomeRun/1.0"


async def _delayed_release(manager: TunerManager, lease: Lease, grace: float) -> None:
    await asyncio.sleep(max(0.0, grace))
    await manager.release(lease)


async def _proxy_iter(
    request: Request, manager: TunerManager, lease: Lease
) -> AsyncIterator[bytes]:
    """Relay the encoder's MPEG-TS to the client, tracking bytes for lifecycle."""
    url = lease.tuner.stream_endpoint
    # follow_redirects: some encoders 301/302 (trailing slash, http→https).
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(None, connect=15.0),
        follow_redirects=True,
    )
    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                logger.warning(
                    "Stream %s encoder returned HTTP %s for %s (after redirects)",
                    lease.tune_id,
                    resp.status_code,
                    url,
                )
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(_CHUNK):
                if await request.is_disconnected():
                    break
                manager.touch(lease.tune_id, len(chunk))
                yield chunk
    except (httpx.HTTPError, asyncio.CancelledError, GeneratorExit) as exc:
        detail = ""
        if isinstance(exc, httpx.HTTPStatusError):
            detail = f" HTTP {exc.response.status_code}"
        logger.info("Stream %s ended: %s%s", lease.tune_id, type(exc).__name__, detail)
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


async def open_hdhr_stream(
    request: Request,
    manager: TunerManager,
    channel,
    *,
    tuner_index: Optional[int] = None,
) -> Response:
    """HDHomeRun tune + always-proxy MPEG-TS (never redirect).

    Redirect mode is skipped for HDHR routes so tuner lock lifecycle stays
    tied to the HTTP connection Channels / Plex holds open.
    """
    lease = await manager.lease(channel, tuner_index=tuner_index)
    return StreamingResponse(
        _proxy_iter(request, manager, lease),
        media_type="video/mp2t",
        headers={"Server": HDHR_SERVER_HEADER},
    )
