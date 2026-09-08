"""Tests for dynamic / lane URL resolution."""

from __future__ import annotations

import httpx
import pytest

from apituner.config import ConfigStore
from apituner.dynamic_url import (
    DynamicUrlError,
    looks_like_dynamic_url,
    resolve_dynamic_url,
)
from apituner.models import Channel, GlobalOptions
from apituner.tuner_manager import TunerManager


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


@pytest.mark.asyncio
async def test_dynamic_url_none_is_no_event():
    url = "http://192.0.2.10:6656/api/adb/lanes/max/1/deeplink?format=text"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="none"))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(DynamicUrlError, match="no event on this lane"):
            await resolve_dynamic_url(url, client=client)


@pytest.mark.asyncio
async def test_dynamic_url_json_ok_false():
    url = (
        "http://192.0.2.10:6656/api/adb/lanes/sportscenter/1/deeplink"
        "?format=json&dynamic_url_json_key=deeplink_url"
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"ok": False, "error": "ADB not enabled for provider"}
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(DynamicUrlError, match="no event on this lane"):
            await resolve_dynamic_url(url, client=client)


@pytest.mark.asyncio
async def test_dynamic_url_retries_timeout_then_succeeds():
    url = (
        "http://192.0.2.10:8095/whatson/18"
        "?format=json&include=deeplink&dynamic_url_json_key=deeplink_url"
    )
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        if hits["n"] < 3:
            raise httpx.ConnectTimeout("")
        return httpx.Response(
            200,
            json={"ok": True, "deeplink_url": "https://tv.youtube.com/watch/abc"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resolved = await resolve_dynamic_url(url, client=client, timeout=15.0, attempts=3)
    assert resolved == "https://tv.youtube.com/watch/abc"
    assert hits["n"] == 3


@pytest.mark.asyncio
async def test_dynamic_url_timeout_message_is_not_blank():
    url = "http://192.0.2.10:8095/whatson/18?format=json"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(DynamicUrlError, match=r"timed out after 15s"):
            await resolve_dynamic_url(url, client=client, timeout=15.0, attempts=1)


@pytest.mark.asyncio
async def test_dynamic_url_http_error_does_not_retry():
    url = "http://192.0.2.10:8095/whatson/18?format=json"
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(502, text="bad gateway")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(DynamicUrlError, match="HTTP 502"):
            await resolve_dynamic_url(url, client=client, attempts=3)
    assert hits["n"] == 1


def test_dynamic_url_options_default_and_blank():
    opts = GlobalOptions()
    assert opts.dynamic_url_timeout == 15.0
    assert opts.dynamic_url_attempts == 3
    blank = GlobalOptions(dynamic_url_timeout=None, dynamic_url_attempts="")  # type: ignore[arg-type]
    assert blank.dynamic_url_timeout == 15.0
    assert blank.dynamic_url_attempts == 3
    clamped = GlobalOptions(dynamic_url_timeout=0, dynamic_url_attempts=99)
    assert clamped.dynamic_url_timeout == 1.0
    assert clamped.dynamic_url_attempts == 10
    wild = GlobalOptions(
        dynamic_url_timeout=float("nan"),
        dynamic_url_attempts=float("nan"),
    )
    assert wild.dynamic_url_timeout == 15.0
    assert wild.dynamic_url_attempts == 3
    inf = GlobalOptions(dynamic_url_timeout=float("inf"))
    assert inf.dynamic_url_timeout == 15.0


@pytest.mark.asyncio
async def test_resolve_launch_url_uses_options(tmp_path, monkeypatch):
    store = ConfigStore(data_dir=tmp_path)
    store.config.options = GlobalOptions(
        dynamic_url_timeout=8.0, dynamic_url_attempts=2
    )
    manager = TunerManager(store)
    captured: dict = {}

    async def fake_resolve(url, *, timeout=10.0, attempts=3, client=None):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["attempts"] = attempts
        return "https://tv.youtube.com/watch/abc"

    monkeypatch.setattr("apituner.tuner_manager.resolve_dynamic_url", fake_resolve)
    channel = Channel(
        number="1",
        name="Sports 1",
        package_name="com.google.android.youtube.tvunplugged",
        url="http://192.0.2.10:8095/whatson/1?format=json",
    )
    got = await manager._resolve_launch_url(channel)
    assert got == "https://tv.youtube.com/watch/abc"
    assert captured["timeout"] == 8.0
    assert captured["attempts"] == 2
