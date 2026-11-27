"""Encoder recovery and shared stream behavior through the HTTP handlers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from apituner.config import ConfigStore
from apituner.models import Channel, ControlConfig, Tuner
from apituner.stream import _NULL_PACKETS, open_hdhr_stream, open_stream
from apituner.tuner_manager import NoTunerAvailable, TunerInUse, TunerManager


def _video(value: int) -> bytes:
    # Two MPEG-TS packets with identifiable payloads.
    return (b"\x47\x01\x00\x10" + bytes([value]) * 184) * 2


class EncoderBody(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.closed = asyncio.Event()

    async def __aiter__(self):
        while (chunk := await self.chunks.get()) is not None:
            yield chunk

    async def aclose(self) -> None:
        self.closed.set()


class Viewer:
    def __init__(self, response) -> None:
        self.disconnected = asyncio.Event()
        self.messages: asyncio.Queue[dict] = asyncio.Queue()
        self.task = asyncio.create_task(response(
            {"type": "http", "asgi": {"spec_version": "2.3"}},
            self.receive,
            self.messages.put,
        ))

    async def receive(self):
        await self.disconnected.wait()
        return {"type": "http.disconnect"}

    async def next_message(self):
        return await asyncio.wait_for(self.messages.get(), 2)

    async def next_video(self):
        while True:
            message = await self.next_message()
            if message["type"] == "http.response.body" and message["body"] != _NULL_PACKETS:
                return message["body"]

    async def close(self):
        self.disconnected.set()
        await asyncio.wait_for(self.task, 2)


@pytest.fixture
def encoder(monkeypatch):
    def install(handler):
        transport = httpx.MockTransport(handler)

        class EncoderClient(httpx.AsyncClient):
            def __init__(self, **kwargs):
                super().__init__(**kwargs, transport=transport)

        monkeypatch.setattr("apituner.stream.httpx.AsyncClient", EncoderClient)
        monkeypatch.setattr("apituner.stream._POLL_SECONDS", 0.01)
        monkeypatch.setattr("apituner.stream._ENCODER_STALL_SECONDS", 0.04)

    return install


@pytest.fixture
async def tuner_setup(tmp_path):
    store = ConfigStore(tmp_path)
    store.config.options.encoder_recovery_seconds = 1
    store.config.options.release_grace_seconds = 0.15
    store.config.options.stop_on_release = True
    store.config.tuners = [
        Tuner(name=f"Tuner {index}", control=ControlConfig(type="http_agent", host="tv"),
              stream_endpoint=f"http://encoder/stream{index}")
        for index in range(2)
    ]
    manager = TunerManager(store)
    backend = SimpleNamespace(stop=AsyncMock())
    for tuner in store.config.tuners:
        manager._backends[tuner.id] = backend
    tune = AsyncMock(return_value=None)
    manager._do_tune = tune
    store.config.options.stream_during_tune = False
    channel = Channel(number="1", name="Channel", package_name="com.example.tv", url="app://one")
    try:
        yield manager, store, channel, tune, backend
    finally:
        manager._backends.clear()
        await manager.stop_reaper()


async def test_routes_share_a_tune_and_keep_it_until_last_disconnect(tuner_setup, encoder):
    manager, store, channel, tune, backend = tuner_setup
    body = EncoderBody()
    get_encoder = AsyncMock(return_value=httpx.Response(200, stream=body))
    encoder(get_encoder)

    m3u, hdhr = await asyncio.gather(
        open_stream(None, manager, channel),
        open_hdhr_stream(None, manager, channel, tuner_index=0),
    )
    first, second = Viewer(m3u), Viewer(hdhr)
    try:
        headers = await second.next_message()
        assert (b"server", b"HDHomeRun/1.0") in headers["headers"]
        body.chunks.put_nowait(_video(1))
        assert await first.next_video() == await second.next_video() == _video(1)
        assert tune.await_count == get_encoder.await_count == 1
        assert [row["locked"] for row in manager.status()] == [True, False]
        different_channel = channel.model_copy(update={"id": "other"})
        with pytest.raises(TunerInUse):
            await open_hdhr_stream(None, manager, different_channel, tuner_index=0)
        with pytest.raises(NoTunerAvailable):
            await open_hdhr_stream(None, manager, channel, tuner_index=2)

        await first.close()
        body.chunks.put_nowait(_video(2))
        assert await second.next_video() == _video(2)
        backend.stop.assert_not_awaited()
        await second.close()
        assert manager.status()[0]["locked"]

        returning = Viewer(await open_stream(None, manager, channel))
        try:
            body.chunks.put_nowait(_video(3))
            assert await returning.next_video() == _video(3)
            assert tune.await_count == get_encoder.await_count == 1
        finally:
            await returning.close()
        session = next(iter(manager.stream_relay.sessions.values()))
        await asyncio.wait_for(session.task, 2)
        assert body.closed.is_set()
        assert not manager.status()[0]["locked"]
        backend.stop.assert_awaited_once()
    finally:
        await first.close()
        await second.close()


async def test_encoder_recovery_reconnects_and_aligns_packets(tuner_setup, encoder):
    manager, _, channel, tune, _ = tuner_setup
    original, recovered = EncoderBody(), EncoderBody()
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/stream0":
            return httpx.Response(301, headers={"Location": "/stream0/"})
        count = requests.count("/stream0/")
        if count == 1:
            return httpx.Response(200, stream=original)
        if count == 2:
            return httpx.Response(503)
        return httpx.Response(200, stream=recovered)

    encoder(handler)
    viewer = Viewer(await open_stream(None, manager, channel))
    try:
        original.chunks.put_nowait(_video(1) + _video(4)[:50])
        assert await viewer.next_video() == _video(1)
        # The open encoder connection stalls with a partial packet buffered.
        assert (await viewer.next_message())["body"] == _NULL_PACKETS
        recovered.chunks.put_nowait(_video(4)[50:188] + _video(2)[:120])
        recovered.chunks.put_nowait(_video(2)[120:])
        assert await viewer.next_video() == _video(2)
        assert original.closed.is_set()
        assert requests[:6] == ["/stream0", "/stream0/"] * 3
        assert manager.status()[0]["bytes_transferred"] == len(_video(1) + _video(2))
        tune.assert_awaited_once()
    finally:
        await viewer.close()


async def test_unavailable_encoder_expires_and_releases_tuner(tuner_setup, encoder):
    manager, store, channel, _, backend = tuner_setup
    store.config.options.encoder_recovery_seconds = 0.08
    handler = AsyncMock(return_value=httpx.Response(503))
    encoder(handler)
    viewer = Viewer(await open_hdhr_stream(None, manager, channel))
    session = next(iter(manager.stream_relay.sessions.values()))
    try:
        assert (await viewer.next_message())["status"] == 200
        assert (await viewer.next_message())["body"] == _NULL_PACKETS
        assert await viewer.next_video() == b""
        await asyncio.wait_for(session.task, 2)
        assert handler.await_count > 1
        backend.stop.assert_awaited_once()
        assert not manager.status()[0]["locked"]
    finally:
        await viewer.close()


async def test_disabled_recovery_passes_through_and_ends_at_eof(tuner_setup, encoder):
    manager, store, channel, _, _ = tuner_setup
    store.config.options.encoder_recovery_seconds = 0
    handler = AsyncMock(return_value=httpx.Response(200, content=b"original data"))
    encoder(handler)
    viewer = Viewer(await open_stream(None, manager, channel))
    try:
        assert await viewer.next_video() == b"original data"
        assert await viewer.next_video() == b""
        handler.assert_awaited_once()
    finally:
        await viewer.close()


async def test_hdhr_index_does_not_join_another_tuner(tuner_setup, encoder):
    manager, store, channel, tune, _ = tuner_setup
    store.config.options.stream_mode = "redirect"
    first_encoder, second_encoder = EncoderBody(), EncoderBody()
    encoder(lambda request: httpx.Response(
        200, stream=first_encoder if request.url.path == "/stream0" else second_encoder
    ))
    first = Viewer(await open_hdhr_stream(None, manager, channel, tuner_index=0))
    second = Viewer(await open_hdhr_stream(None, manager, channel, tuner_index=1))
    try:
        first_encoder.chunks.put_nowait(_video(1))
        second_encoder.chunks.put_nowait(_video(2))
        assert await first.next_video() == _video(1)
        assert await second.next_video() == _video(2)
        assert tune.await_count == 2
    finally:
        await first.close()
        await second.close()


async def test_tune_failure_ends_recovery_for_all_viewers(tuner_setup, encoder):
    manager, store, channel, _, backend = tuner_setup
    store.config.options.stream_during_tune = True
    store.config.options.wait_for_playback = True
    body = EncoderBody()
    encoder(lambda request: httpx.Response(200, stream=body))
    first = Viewer(await open_stream(None, manager, channel))
    second = Viewer(await open_hdhr_stream(None, manager, channel))
    session = next(iter(manager.stream_relay.sessions.values()))

    async def failed_tune():
        raise RuntimeError("playback failed")

    try:
        assert (await first.next_message())["status"] == 200
        assert (await first.next_message())["body"] == _NULL_PACKETS
        session.lease.tune_task = asyncio.create_task(failed_tune())
        assert await first.next_video() == await second.next_video() == b""
        await asyncio.wait_for(session.task, 2)
        assert body.closed.is_set()
        backend.stop.assert_awaited_once()
    finally:
        await first.close()
        await second.close()
