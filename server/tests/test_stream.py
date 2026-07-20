"""Encoder stream proxy follows redirects and yields MPEG-TS bytes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from apituner.stream import _proxy_iter


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_proxy_iter_follows_301(monkeypatch: pytest.MonkeyPatch):
    payload = b"\x47" * 188  # one MPEG-TS packet
    hops = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hops["n"] += 1
        if request.url.path == "/live/stream1":
            return httpx.Response(
                301,
                headers={"Location": str(request.url.copy_with(path="/live/stream1/"))},
            )
        return httpx.Response(200, content=payload)

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("apituner.stream.httpx.AsyncClient", PatchedClient)

    manager = SimpleNamespace(
        touch=lambda *_a, **_k: None,
        options=SimpleNamespace(release_grace_seconds=0.0),
        release=AsyncMock(),
    )
    lease = SimpleNamespace(
        tune_id="abc123",
        tuner=SimpleNamespace(stream_endpoint="http://192.0.2.10/live/stream1"),
    )

    chunks: list[bytes] = []
    async for chunk in _proxy_iter(_FakeRequest(), manager, lease):  # type: ignore[arg-type]
        chunks.append(chunk)

    assert b"".join(chunks) == payload
    assert hops["n"] >= 2
