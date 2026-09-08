"""HTTP API tests for channel CRUD."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from apituner.channels import ChannelValidationError
from apituner.config import ConfigStore
from apituner.models import Channel, ControlConfig, GlobalOptions, Tuner


def _channel(number: int | str, name: str, *, cid: str) -> Channel:
    return Channel(
        id=cid,
        number=number,
        name=name,
        package_name="com.example.app",
    )


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APITUNER_DATA_DIR", str(tmp_path))
    store = ConfigStore(data_dir=tmp_path)
    store.config.options = GlobalOptions(
        hdhr_enabled=False,
        hdhr_ssdp_enabled=False,
        hdhr_udp_discovery_enabled=False,
    )
    store.config.tuners = [
        Tuner(
            name="T1",
            control=ControlConfig(type="http_agent", host="127.0.0.1", port=9092),
            stream_endpoint="http://127.0.0.1:8090/stream0",
        )
    ]
    store.config.channels = [
        _channel(1, "Keep Me", cid="a" * 32),
        _channel(2, "Other", cid="b" * 32),
    ]
    store.save()

    from apituner.main import app

    with TestClient(app) as client:
        yield client


def test_status_includes_m3u_url(api_client: TestClient):
    resp = api_client.get("/api/status")
    assert resp.status_code == 200
    assert resp.json()["m3u_url"].endswith("/channels.m3u8")


def test_update_channel_rolls_back_on_validation_failure(api_client: TestClient):
    channel_id = "a" * 32
    with patch(
        "apituner.main.validate_unique_ids",
        side_effect=ChannelValidationError("duplicate ids"),
    ):
        resp = api_client.put(
            f"/api/channels/{channel_id}",
            json={
                "number": "9",
                "name": "Broken",
                "package_name": "com.example.app",
            },
        )
    assert resp.status_code == 409

    listed = api_client.get("/api/channels").json()
    original = next(ch for ch in listed if ch["id"] == channel_id)
    assert original["name"] == "Keep Me"
    assert original["number"] == "1"


def test_m3u_provider_filter(api_client: TestClient):
    resp = api_client.get("/channels.m3u8?provider=Keep%20Me")
    assert resp.status_code == 200
    assert resp.text.count("#EXTINF:") == 0

    store = api_client.app.state.store
    ch = store.config.channels[0]
    ch.provider_name = "YouTube TV"
    store.save()

    resp = api_client.get("/channels.m3u8?provider=YouTube%20TV")
    assert resp.status_code == 200
    assert "YouTube TV" in resp.text or "Keep Me" in resp.text
    assert resp.text.count("#EXTINF:") == 1

    resp = api_client.get("/channels.m3u8?provider=youtube%20tv")
    assert resp.status_code == 200
    assert resp.text.count("#EXTINF:") == 1


def test_update_channel_returns_saved_row(api_client: TestClient):
    channel_id = "a" * 32
    resp = api_client.put(
        f"/api/channels/{channel_id}",
        json={
            "number": "100.1",
            "name": "Updated",
            "package_name": "com.example.app",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == channel_id
    assert body["number"] == "100.1"
    assert body["name"] == "Updated"


def test_update_channel_preserves_source_when_omitted(api_client: TestClient):
    channel_id = "a" * 32
    store = api_client.app.state.store
    store.config.channels[0].source = "yttv-sports"
    store.save()

    resp = api_client.put(
        f"/api/channels/{channel_id}",
        json={
            "number": "1",
            "name": "Keep Me",
            "package_name": "com.example.app",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["source"] == "yttv-sports"
