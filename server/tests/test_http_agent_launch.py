"""Agent launch must fail the tune on HTTP 4xx or success:false."""

from __future__ import annotations

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
async def test_launch_ok_on_success_true():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "message": "launched"})

    backend = _backend_with_transport(handler)
    await backend.launch(package="com.espn.score_center")
    await backend.close()
