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


async def _redirect_release(manager: TunerManager, lease: Lease, idle: float) -> None:
    """For redirect mode: finish App Play first, then idle-reclaim."""
    task = lease.tune_task
    if task is not None:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    await asyncio.sleep(max(0.0, idle))
    await manager.release(lease)


def _tune_failed(lease: Lease) -> Optional[BaseException]:
    """Return the exception if a background tune task finished with failure."""
    for task in (getattr(lease, "tune_task", None), getattr(lease, "monitor_task", None)):
        if task is not None and task.done():
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                continue
            if exc is not None:
                return exc
    return None


async def _proxy_iter(
    request: Request, manager: TunerManager, lease: Lease
) -> AsyncIterator[bytes]:
    """Relay the encoder's MPEG-TS to the client, tracking bytes for lifecycle.

    When ``lease.tune_task`` is set (stream_during_tune / App Play or deeplink
    playback wait), bytes flow immediately so Channels does not hit its ~30s
    connect timeout while D-pad navigation or deeplink relaunch runs. If the
    background tune fails, the proxy stops.
    """
    url = lease.tuner.stream_endpoint
    # follow_redirects: some encoders 301/302 (trailing slash, http→https).
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=15.0),
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
            iterator = resp.aiter_bytes(_CHUNK).__aiter__()
            pending = None
            try:
                while True:
                    failed = _tune_failed(lease)
                    if failed is not None or await request.is_disconnected():
                        break
                    active = getattr(manager, "owns_lease", None)
                    if active is not None and not active(lease):
                        break
                    if pending is None:
                        pending = asyncio.create_task(iterator.__anext__())
                    done, _ = await asyncio.wait({pending}, timeout=1)
                    if not done:
                        continue
                    try:
                        chunk = pending.result()
                    except StopAsyncIteration:
                        break
                    pending = None
                    manager.touch(lease.tune_id, len(chunk))
                    yield chunk
            finally:
                if pending is not None:
                    pending.cancel()
                    try:
                        await pending
                    except (asyncio.CancelledError, StopAsyncIteration, httpx.HTTPError):
                        pass

    except (httpx.HTTPError, asyncio.CancelledError, GeneratorExit) as exc:
        detail = ""
        if isinstance(exc, httpx.HTTPStatusError):
            detail = f" HTTP {exc.response.status_code}"
        logger.info("Stream %s ended: %s%s", lease.tune_id, type(exc).__name__, detail)
    finally:
        await client.aclose()
        # Release after a short grace, matching ADBTuner's ~5s unlock delay.
        # Cancels an in-flight App Play tune if the client already left.
        asyncio.create_task(
            _delayed_release(manager, lease, manager.options.release_grace_seconds)
        )


async def open_stream(request: Request, manager: TunerManager, channel) -> Response:
    """Acquire a tuner, tune the channel, and return a stream response."""
    lease = await manager.lease(channel)

    if manager.options.stream_mode == "redirect" and channel.source != "fastchannels":
        # Video flows encoder -> Channels directly; reclaim after App Play
        # finishes (if any) plus the idle window.
        asyncio.create_task(
            _redirect_release(manager, lease, manager.options.tuner_idle_timeout_seconds)
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
