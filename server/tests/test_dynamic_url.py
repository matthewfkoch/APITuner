"""Tests for dynamic / lane URL resolution."""

from __future__ import annotations

import httpx
import pytest

from apituner.dynamic_url import (
    DynamicUrlError,
    looks_like_dynamic_url,
    resolve_dynamic_url,
)


def test_looks_like_dynamic_url():
    assert looks_like_dynamic_url(
        "http://192.168.1.74:6656/api/adb/lanes/max/1/deeplink?format=text"
    )
    assert looks_like_dynamic_url(
        "http://host/whatson/2?deeplink=1&dynamic_url_json_key=deeplink_url"
    )
    assert looks_like_dynamic_url(
        "http://192.168.1.74:6656/api/adb/lanes/sportscenter/1/deeplink"
        "?format=json&dynamic_url_json_key=deeplink"
    )
    assert not looks_like_dynamic_url("https://play.hbomax.com/channel/watch/abc")
    assert not looks_like_dynamic_url("0")
    assert not looks_like_dynamic_url("sportscenter://x-callback-url/show")


@pytest.mark.asyncio
async def test_dynamic_url_text(httpx_mock=None):
    url = "http://192.0.2.10:6656/api/adb/lanes/max/1/deeplink?format=text"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, text="https://play.max.com/watch/abc\n"
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        resolved = await resolve_dynamic_url(url, client=client)
    assert resolved == "https://play.max.com/watch/abc"


@pytest.mark.asyncio
async def test_dynamic_url_json_key():
    url = (
        "http://192.0.2.10:6656/api/adb/lanes/sportscenter/1/deeplink"
        "?format=json&dynamic_url_json_key=deeplink"
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"ok": True, "deeplink": "sportscenter://x-callback-url/showWatchStream?playID=1"},
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        # Ensure dynamic_url_json_key was stripped from the request.
        resolved = await resolve_dynamic_url(url, client=client)
    assert resolved.startswith("sportscenter://")


@pytest.mark.asyncio
async def test_dynamic_url_empty_fails():
    url = "http://192.0.2.10:6656/api/adb/lanes/max/1/deeplink?format=text"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=""))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(DynamicUrlError, match="empty"):
            await resolve_dynamic_url(url, client=client)
