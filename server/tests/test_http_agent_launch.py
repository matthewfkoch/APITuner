"""Agent launch must fail the tune on HTTP 4xx or success:false."""

from __future__ import annotations

import json

import httpx
import pytest

from apituner.backends.base import BackendUnavailable
from apituner.backends.http_agent import HttpAgentBackend
from apituner.models import ControlConfig, Tuner


def _backend_with_transport(handler) -> HttpAgentBackend:
    tuner = Tuner(
        id="t1",
        name="onn",
        control=ControlConfig(type="http_agent", host="192.0.2.1", port=9092),
        stream_endpoint="http://192.0.2.2/s",
    )
    backend = HttpAgentBackend(tuner)
    backend._client = httpx.AsyncClient(
        base_url="http://192.0.2.1:9092",
        transport=httpx.MockTransport(handler),
    )
    return backend


@pytest.mark.asyncio
async def test_launch_package_only_uses_api_launch():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"success": True, "message": "launched"})

    backend = _backend_with_transport(handler)
    await backend.launch(package="com.espn.score_center")
    assert paths == ["/api/launch"]
    await backend.close()


@pytest.mark.asyncio
async def test_launch_deeplink_uses_launch_intent():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"success": True, "message": "launched"})

    backend = _backend_with_transport(handler)
    await backend.launch(
        package="com.wbd.stream",
        deeplink="https://play.hbomax.com/x",
        action="android.intent.action.VIEW",
    )
    assert paths == ["/api/launch-intent"]
    await backend.close()


@pytest.mark.asyncio
async def test_launch_deeplink_sends_clear_task():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"success": True, "message": "launched"})

    backend = _backend_with_transport(handler)
    await backend.launch(
        package="com.att.tv",
        deeplink="https://stream.directv.com/watch/81",
        clear_task=True,
    )
    assert bodies[0].get("clearTask") is True
    await backend.close()


@pytest.mark.asyncio
async def test_launch_rejects_http_400():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"success": False, "message": "bad request"})

    backend = _backend_with_transport(handler)
    with pytest.raises(BackendUnavailable, match="bad request"):
        await backend.launch(
            package="com.wbd.stream",
            deeplink="https://play.hbomax.com/x",
        )
    await backend.close()


@pytest.mark.asyncio
async def test_launch_rejects_success_false():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "message": "failed"})

    backend = _backend_with_transport(handler)
    with pytest.raises(BackendUnavailable, match="could not open app|failed"):
        await backend.launch(package="com.espn.score_center")
    await backend.close()


@pytest.mark.asyncio
async def test_stop_sends_empty_body_so_keepalive_is_not_poisoned():
    """Agent <=0.1.27 does not read the /api/stop body. '{}' becomes '{}POST'."""
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        assert request.headers.get("content-length") == "0"
        return httpx.Response(200, json={"success": True, "message": "sent HOME"})

    backend = _backend_with_transport(handler)
    await backend.stop()
    assert seen == [b""]
    await backend.close()


@pytest.mark.asyncio
async def test_launch_ok_on_success_true():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "message": "launched"})

    backend = _backend_with_transport(handler)
    await backend.launch(package="com.espn.score_center")
    await backend.close()


@pytest.mark.asyncio
async def test_typed_extras_preserve_boolean_types_and_url_punctuation():
    launches = []

    def handler(request):
        if request.url.path == "/api/info":
            return httpx.Response(200, json={"capabilities": {"typed_intent_extras": True}})
        launches.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    backend = _backend_with_transport(handler)
    extras = {
        "drm": True, "captions": False,
        "stream_url": "http://server/play/a,b.mpd?token=a:b&x=%2F",
        "title": "News: live, 日本語",
    }
    try:
        await backend.launch(package="com.fastchannels.player", intent_extras=extras)
        assert launches == [{"packageName": "com.fastchannels.player", "intent_extras": extras}]
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_typed_extras_refuse_old_agent_before_launch():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"capabilities": {}})

    backend = _backend_with_transport(handler)
    try:
        with pytest.raises(BackendUnavailable, match="Update APITuner Agent"):
            await backend.launch(package="com.fastchannels.player", intent_extras={"drm": True})
        assert paths == ["/api/info"]
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1, None, [], {}])
async def test_typed_extras_reject_unsupported_values(value):
    def handler(request):
        pytest.fail("Invalid extras must not issue a request")

    backend = _backend_with_transport(handler)
    try:
        with pytest.raises(BackendUnavailable, match="strings or booleans"):
            await backend.launch(package="com.fastchannels.player", intent_extras={"drm": value})
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_hybrid_typed_launch_never_falls_back_to_keys_backend():
    from unittest.mock import AsyncMock
    from apituner.backends.hybrid import SplitControlBackend

    primary = _backend_with_transport(lambda request: httpx.Response(200, json={"capabilities": {}}))
    keys = AsyncMock()
    backend = SplitControlBackend(primary, keys)
    try:
        with pytest.raises(BackendUnavailable, match="Update APITuner Agent"):
            await backend.launch(package="com.fastchannels.player", intent_extras={"drm": True})
        keys.launch.assert_not_called()
    finally:
        await primary.close()
