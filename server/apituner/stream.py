"""MPEG-TS relay, shared tuner sessions, and stream routes."""

from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing
from typing import TYPE_CHECKING, AsyncIterator, Optional
from weakref import WeakValueDictionary

import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send

if TYPE_CHECKING:
    from .models import Channel
    from .tuner_manager import Lease, TunerManager

logger = logging.getLogger(__name__)

_PACKET_SIZE = 188
_SYNC_BYTE = b"\x47"
_NULL_PACKETS = (b"\x47\x1f\xff\x10" + b"\xff" * 184) * 7
_POLL_SECONDS = 0.5
_ENCODER_STALL_SECONDS = 3.0
_MAX_CHUNK_BYTES = (64 * 1024 // _PACKET_SIZE) * _PACKET_SIZE
_VIEWER_BUFFER_CHUNKS = 32
HDHR_SERVER_HEADER = "HDHomeRun/1.0"


async def _redirect_release(manager: TunerManager, lease: Lease, idle: float) -> None:
    """For redirect mode, finish App Play first, then idle-reclaim."""
    task = lease.tune_task
    if task is not None:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    await asyncio.sleep(max(0.0, idle))
    await manager.release(lease)


def _tune_failed(lease: Lease) -> Optional[BaseException]:
    for task in (getattr(lease, "tune_task", None), getattr(lease, "monitor_task", None)):
        if task is not None and task.done() and not task.cancelled():
            if (error := task.exception()) is not None:
                return error
    return None


def _packet_start(data: bytes) -> int:
    """Find two consecutive sync bytes at the MPEG-TS packet spacing."""
    offset = data.find(_SYNC_BYTE)
    while 0 <= offset < len(data) - _PACKET_SIZE:
        if data[offset + _PACKET_SIZE] == _SYNC_BYTE[0]:
            return offset
        offset = data.find(_SYNC_BYTE, offset + 1)
    return -1


async def _encoder_chunks(
    client: httpx.AsyncClient, url: str, *, frame_packets: bool
) -> AsyncIterator[bytes]:
    # Start each connection with fresh framing. A disconnect can truncate a
    # packet, and a reconnect can start partway through the encoder's output.
    remainder = b""
    synced = False
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            if not frame_packets:
                for offset in range(0, len(chunk), _MAX_CHUNK_BYTES):
                    yield chunk[offset:offset + _MAX_CHUNK_BYTES]
                continue
            data = remainder + chunk
            if not synced:
                start = _packet_start(data)
                if start < 0:
                    remainder = data[-_PACKET_SIZE:]
                    continue
                synced = True
                data = data[start:]
            end = len(data) // _PACKET_SIZE * _PACKET_SIZE
            remainder = data[end:]
            for offset in range(0, end, _MAX_CHUNK_BYTES):
                yield data[offset:min(end, offset + _MAX_CHUNK_BYTES)]


async def _read_encoder(
    lease: Lease, recovery_seconds: float
) -> AsyncIterator[bytes]:
    """Reconnect on EOF, stalls, and transient HTTP failures."""
    timeout = min(_ENCODER_STALL_SECONDS, recovery_seconds / 2) if recovery_seconds else 15.0
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        while True:
            reason = "end of stream"
            try:
                async with aclosing(_encoder_chunks(
                    client, lease.tuner.stream_endpoint, frame_packets=bool(recovery_seconds)
                )) as chunks:
                    while True:
                        if recovery_seconds:
                            # Bound time to a complete packet even if the HTTP
                            # peer keeps sending incomplete data.
                            async with asyncio.timeout(timeout):
                                chunk = await anext(chunks, None)
                        else:
                            chunk = await anext(chunks, None)
                        if chunk is None:
                            break
                        yield chunk
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                reason = f"HTTP {status}"
                if status < 500 and status not in (408, 429):
                    logger.warning("Stream %s encoder returned %s", lease.tune_id, reason)
                    return
            except (httpx.TransportError, TimeoutError) as exc:
                reason = type(exc).__name__
            except httpx.HTTPError as exc:
                logger.warning("Stream %s encoder failed (%s)", lease.tune_id, type(exc).__name__)
                return
            if not recovery_seconds:
                logger.info("Stream %s encoder closed (%s)", lease.tune_id, reason)
                return
            logger.debug("Stream %s reconnecting to encoder (%s)", lease.tune_id, reason)
            await asyncio.sleep(_POLL_SECONDS)


async def _relay_encoder(
    manager: TunerManager, lease: Lease, recovery_seconds: float
) -> AsyncIterator[bytes]:
    """Feed one encoder to all viewers, inserting null packets during outages."""
    loop = asyncio.get_running_loop()
    last_video = loop.time()
    filling = False
    async with aclosing(_read_encoder(lease, recovery_seconds)) as encoder:
        pending = None
        try:
            while manager.owns_lease(lease) and _tune_failed(lease) is None:
                delay = _POLL_SECONDS
                if recovery_seconds:
                    remaining = last_video + recovery_seconds - loop.time()
                    if remaining <= 0:
                        logger.warning("Stream %s exceeded encoder recovery window", lease.tune_id)
                        return
                    delay = min(delay, remaining)
                if pending is None:
                    pending = asyncio.create_task(anext(encoder, None))
                done, _ = await asyncio.wait({pending}, timeout=delay)
                if not manager.owns_lease(lease) or _tune_failed(lease) is not None:
                    return
                if done:
                    chunk = pending.result()
                    pending = None
                    if chunk is None:
                        return
                    if filling:
                        logger.info("Stream %s encoder video resumed", lease.tune_id)
                    last_video = loop.time()
                    filling = False
                    manager.touch(lease.tune_id, len(chunk))
                    yield chunk
                elif recovery_seconds and loop.time() < last_video + recovery_seconds:
                    if not filling:
                        logger.debug("Stream %s waiting for encoder video", lease.tune_id)
                    filling = True
                    manager.touch(lease.tune_id, 0)
                    yield _NULL_PACKETS
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


class _RelaySession:
    """One tuner lease and encoder reader, with bounded buffers for its viewers."""

    def __init__(self, relay: StreamRelay, lease: Lease) -> None:
        self.relay = relay
        self.lease = lease
        self.viewers: set[asyncio.Queue[Optional[bytes]]] = set()
        self.closing = False
        self._started = False
        self._idle_timer: Optional[asyncio.TimerHandle] = None
        self._recovery_seconds = relay.manager.options.encoder_recovery_seconds
        self.task = asyncio.create_task(self._run(), name=f"relay-{lease.tune_id}")

    def subscribe(self) -> asyncio.Queue[Optional[bytes]]:
        self._cancel_idle_timer()
        # Leave one slot for the end marker when the buffer is full.
        queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(_VIEWER_BUFFER_CHUNKS + 1)
        self.viewers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Optional[bytes]]) -> None:
        self.viewers.discard(queue)
        if not self.viewers and not self.closing and self._idle_timer is None:
            grace = self.relay.manager.options.release_grace_seconds
            self._idle_timer = asyncio.get_running_loop().call_later(max(0.0, grace), self.stop)

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def stop(self) -> None:
        if self.closing:
            return
        self.closing = True
        # Let _run enter its finally block even if stopped before its first turn.
        if self._started:
            self.task.cancel()

    async def _run(self) -> None:
        self._started = True
        try:
            if self.closing:
                return
            async with aclosing(_relay_encoder(
                self.relay.manager, self.lease, self._recovery_seconds
            )) as chunks:
                async for chunk in chunks:
                    for queue in tuple(self.viewers):
                        if queue.qsize() >= _VIEWER_BUFFER_CHUNKS:
                            logger.debug("Stream %s dropped a slow viewer", self.lease.tune_id)
                            queue.put_nowait(None)
                            self.unsubscribe(queue)
                        else:
                            queue.put_nowait(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Stream %s relay failed", self.lease.tune_id)
        finally:
            self.closing = True
            self._cancel_idle_timer()
            for queue in self.viewers:
                queue.put_nowait(None)
            self.viewers.clear()
            try:
                await self.relay.manager.release(self.lease)
            finally:
                self.relay.sessions.pop(self.lease.tune_id, None)


class _RelayResponse(StreamingResponse):
    def __init__(self, session: _RelaySession, headers: Optional[dict[str, str]]) -> None:
        self._session = session
        self._queue = session.subscribe()
        super().__init__(self._body(), media_type="video/mp2t", headers=headers)

    async def _body(self) -> AsyncIterator[bytes]:
        while (chunk := await self._queue.get()) is not None:
            yield chunk

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also detach when the initial header send fails or Starlette
            # cancels a response before it starts iterating its body.
            self._session.unsubscribe(self._queue)


class StreamRelay:
    def __init__(self, manager: TunerManager) -> None:
        self.manager = manager
        self.sessions: dict[str, _RelaySession] = {}
        self._channel_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def open(
        self,
        channel: Channel,
        *,
        tuner_index: Optional[int] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Response:
        # Serialize acquisition only for this channel. Concurrent clients can
        # join the same tune as soon as the first request has acquired its lease.
        lock = self._channel_locks.get(channel.id)
        if lock is None:
            lock = self._channel_locks[channel.id] = asyncio.Lock()
        async with lock:
            tuner = self.manager.tuner_at_index(tuner_index) if tuner_index is not None else None
            for session in self.sessions.values():
                lease = session.lease
                if (
                    channel.available
                    and not session.closing
                    and lease.channel == channel
                    and (tuner_index is None or (tuner is not None and tuner.id == lease.tuner.id))
                    and self.manager.owns_lease(lease)
                    and _tune_failed(lease) is None
                ):
                    return _RelayResponse(session, headers)
            lease = await self.manager.lease(channel, tuner_index=tuner_index)
            session = _RelaySession(self, lease)
            self.sessions[lease.tune_id] = session
            return _RelayResponse(session, headers)

    async def close(self) -> None:
        sessions = list(self.sessions.values())
        for session in sessions:
            session.stop()
        await asyncio.gather(*(session.task for session in sessions), return_exceptions=True)


async def open_stream(request: Request, manager: TunerManager, channel: Channel) -> Response:
    if manager.options.stream_mode == "redirect" and channel.source != "fastchannels":
        lease = await manager.lease(channel)
        asyncio.create_task(
            _redirect_release(manager, lease, manager.options.tuner_idle_timeout_seconds)
        )
        return RedirectResponse(lease.tuner.stream_endpoint, status_code=302)

    return await manager.stream_relay.open(channel)


async def open_hdhr_stream(
    request: Request,
    manager: TunerManager,
    channel: Channel,
    *,
    tuner_index: Optional[int] = None,
) -> Response:
    return await manager.stream_relay.open(
        channel, tuner_index=tuner_index, headers={"Server": HDHR_SERVER_HEADER}
    )
