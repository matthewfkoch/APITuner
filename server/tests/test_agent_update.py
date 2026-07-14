"""Tests for Agent latest.json caching and dashboard update-agent push."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from apituner.agent_update import (
    AgentLatest,
    AgentLatestCache,
    AgentUpdateError,
    download_apk,
)
from apituner.backends.base import DeviceInfo
from apituner.config import ConfigStore
from apituner.models import Channel, ControlConfig, Tuner


def _tuner(tid: str = "t1", *, backend: str = "http_agent") -> Tuner:
    return Tuner(
        id=tid,
        name=f"Tuner {tid}",
        enabled=True,
        stream_endpoint="http://127.0.0.1/stream.ts",
        control=ControlConfig(type=backend, host="192.0.2.10", port=9092),
    )


def _channel(number: int = 1, name: str = "ABC") -> Channel:
    return Channel(
        number=number,
        name=name,
        package_name="com.example.app",
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APITUNER_DATA_DIR", str(tmp_path))
    store = ConfigStore(data_dir=tmp_path)
    store.config.options.hdhr_enabled = False
    store.config.options.hdhr_ssdp_enabled = False
    store.config.options.hdhr_udp_discovery_enabled = False
    store.config.tuners = [_tuner("agent1"), _tuner("remote1", backend="androidtv_remote")]
    store.config.channels = [_channel()]
    store.save()

    from apituner.agent_update import latest_cache
    from apituner.main import app

    latest_cache.clear()
    with TestClient(app) as test_client:
        yield test_client
    latest_cache.clear()


@pytest.mark.asyncio
async def test_agent_latest_cache_ttl(monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0}

    async def fake_fetch(url: str) -> AgentLatest:
        calls["n"] += 1
        return AgentLatest(
            version_name="0.1.4",
            version_code=7,
            apk_url="https://example.com/a.apk",
        )

    monkeypatch.setattr("apituner.agent_update.fetch_latest", fake_fetch)
    cache = AgentLatestCache(url="https://example.com/latest.json", ttl_seconds=60)
    a = await cache.get()
    b = await cache.get()
    assert a.version_code == 7
    assert b.version_code == 7
    assert calls["n"] == 1
    c = await cache.get(force=True)
    assert c.version_code == 7
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_download_apk_verifies_sha256(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    body = b"apk-bytes-here"
    digest = hashlib.sha256(body).hexdigest()
    latest = AgentLatest(
        version_name="0.1.4",
        version_code=7,
        apk_url="https://example.com/apituner-agent-0.1.4.apk",
        sha256=digest,
        apk_name="apituner-agent-0.1.4.apk",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("apituner.agent_update.httpx.AsyncClient", PatchedClient)
    path = await download_apk(latest, tmp_path / "cache")
    assert path.name == "apituner-agent-0.1.4.apk"
    assert path.read_bytes() == body


@pytest.mark.asyncio
async def test_download_apk_sha_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    latest = AgentLatest(
        version_name="0.1.4",
        version_code=7,
        apk_url="https://example.com/a.apk",
        sha256="deadbeef",
        apk_name="a.apk",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"wrong")

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("apituner.agent_update.httpx.AsyncClient", PatchedClient)
    with pytest.raises(AgentUpdateError, match="sha256"):
        await download_apk(latest, tmp_path / "cache")


def test_api_agent_latest(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    async def fake_get(*, force: bool = False) -> AgentLatest:
        return AgentLatest(
            version_name="0.1.4",
            version_code=7,
            apk_url="https://example.com/a.apk",
            sha256="abc",
            apk_name="a.apk",
        )

    monkeypatch.setattr("apituner.main.latest_cache.get", fake_get)
    resp = client.get("/api/agent/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["versionName"] == "0.1.4"
    assert data["versionCode"] == 7
    assert data["apkUrl"].endswith("a.apk")


def test_update_agent_rejects_remote_backend(client: TestClient):
    resp = client.post("/api/tuners/remote1/update-agent")
    assert resp.status_code == 400
    assert "http_agent" in resp.json()["detail"]


def test_update_agent_push(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    async def fake_get(*, force: bool = False) -> AgentLatest:
        return AgentLatest(
            version_name="0.1.4",
            version_code=7,
            apk_url="https://example.com/a.apk",
            sha256="abc",
            apk_name="a.apk",
        )

    async def fake_download(latest: AgentLatest, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / "a.apk"
        path.write_bytes(b"apk")
        return path

    uploaded: dict = {}

    async def fake_upload(self, apk_path):
        uploaded["path"] = str(apk_path)
        return {"success": True, "message": "install dialog opened"}

    async def fake_refresh(self, tuner_id: str):
        return DeviceInfo(agent_version_name="0.1.3", agent_version_code=6)

    monkeypatch.setattr("apituner.main.latest_cache.get", fake_get)
    monkeypatch.setattr("apituner.main.download_apk", fake_download)
    monkeypatch.setattr(
        "apituner.backends.http_agent.HttpAgentBackend.upload_apk",
        fake_upload,
    )
    monkeypatch.setattr(
        "apituner.tuner_manager.TunerManager.refresh_info",
        fake_refresh,
    )

    resp = client.post("/api/tuners/agent1/update-agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["updated"] is True
    assert "install dialog" in data["message"].lower()
    assert uploaded["path"].endswith("a.apk")


def test_update_agent_already_current(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    async def fake_get(*, force: bool = False) -> AgentLatest:
        return AgentLatest(
            version_name="0.1.3",
            version_code=6,
            apk_url="https://example.com/a.apk",
        )

    async def fake_refresh(self, tuner_id: str):
        return DeviceInfo(agent_version_name="0.1.3", agent_version_code=6)

    monkeypatch.setattr("apituner.main.latest_cache.get", fake_get)
    monkeypatch.setattr(
        "apituner.tuner_manager.TunerManager.refresh_info",
        fake_refresh,
    )

    resp = client.post("/api/tuners/agent1/update-agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["updated"] is False
