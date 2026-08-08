"""HTTP tests for pair/auto and Grant permissions on adb tuners."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apituner.adb_grant import GrantResult
from apituner.auto_pair import AutoPairResult
from apituner.config import ConfigStore
from apituner.models import Channel, ControlConfig, Tuner


def _channel() -> Channel:
    return Channel(number=1, name="ABC", package_name="com.example.app")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Prefer http_agent / adb primaries so lifespan refresh_info does not hang
    # on androidtvremote2 TCP connect to DOCUMENTATION IPs.
    monkeypatch.setenv("APITUNER_DATA_DIR", str(tmp_path))

    async def _no_refresh(self, tuner_id: str):
        return None

    monkeypatch.setattr(
        "apituner.tuner_manager.TunerManager.refresh_info",
        _no_refresh,
    )
    store = ConfigStore(data_dir=tmp_path)
    store.config.options.hdhr_enabled = False
    store.config.options.hdhr_ssdp_enabled = False
    store.config.options.hdhr_udp_discovery_enabled = False
    store.config.tuners = [
        Tuner(
            id="hybrid_remote",
            name="Hybrid Remote",
            enabled=True,
            stream_endpoint="http://192.0.2.20/s.ts",
            control=ControlConfig(type="http_agent", host="192.0.2.10", port=9092),
            keys_control=ControlConfig(
                type="androidtv_remote",
                host="192.0.2.10",
                port=6466,
                pair_port=6467,
            ),
        ),
        Tuner(
            id="nostream",
            name="No Stream",
            enabled=True,
            stream_endpoint="http://192.0.2.21/s.ts",
            control=ControlConfig(type="http_agent", host="192.0.2.11", port=9092),
            keys_control=ControlConfig(
                type="androidtv_remote",
                host="192.0.2.11",
                port=6466,
                pair_port=6467,
            ),
        ),
        Tuner(
            id="agent1",
            name="Agent only",
            enabled=True,
            stream_endpoint="http://192.0.2.22/s.ts",
            control=ControlConfig(type="http_agent", host="192.0.2.12", port=9092),
        ),
        Tuner(
            id="adb1",
            name="Fire ADB",
            enabled=True,
            stream_endpoint="http://192.0.2.23/s.ts",
            control=ControlConfig(type="adb", host="192.0.2.13", port=5555),
        ),
        Tuner(
            id="hybrid_fire",
            name="Hybrid Fire",
            enabled=True,
            stream_endpoint="http://192.0.2.24/s.ts",
            control=ControlConfig(type="http_agent", host="192.0.2.14", port=9092),
            keys_control=ControlConfig(
                type="firetv_rest",
                host="192.0.2.14",
                port=8080,
            ),
        ),
    ]
    store.config.channels = [_channel()]
    store.save()
    store.config.tuners[1].stream_endpoint = " "
    store.save()

    from apituner.main import app

    with TestClient(app) as test_client:
        yield test_client


class _FakePairBackend:
    requires_pairing = True
    client_token = None

    async def is_paired(self) -> bool:
        return False

    async def start_pairing(self) -> None:
        return None

    async def finish_pairing(self, pin: str) -> None:
        return None


def test_pair_auto_rejects_agent_only(client: TestClient):
    r = client.post("/api/tuners/agent1/pair/auto", json={})
    assert r.status_code == 400
    assert "does not require pairing" in r.json()["detail"].lower()


def test_pair_auto_requires_stream(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "apituner.tuner_manager.TunerManager.get_pairing_backend",
        lambda self, tuner: _FakePairBackend(),
    )

    async def boom(*_a, **_k):
        raise AssertionError("auto_pair should not run without stream")

    monkeypatch.setattr("apituner.main.auto_pair", boom)
    r = client.post("/api/tuners/nostream/pair/auto", json={})
    assert r.status_code == 400
    assert "stream" in r.json()["detail"].lower()


def test_pair_auto_success(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "apituner.tuner_manager.TunerManager.get_pairing_backend",
        lambda self, tuner: _FakePairBackend(),
    )

    async def fake_auto_pair(backend, stream_url, *, kind, **_kwargs):
        assert kind == "androidtv_remote"
        assert "192.0.2.20" in stream_url
        assert _kwargs.get("already_started") is False
        return AutoPairResult(
            success=True,
            reason="paired",
            pin="A1B2C3",
            pairing_started=True,
            hint="Paired successfully",
        )

    monkeypatch.setattr("apituner.main.auto_pair", fake_auto_pair)
    r = client.post("/api/tuners/hybrid_remote/pair/auto", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["pin"] == "A1B2C3"
    assert data["reason"] == "paired"


def test_pair_auto_already_started(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "apituner.tuner_manager.TunerManager.get_pairing_backend",
        lambda self, tuner: _FakePairBackend(),
    )
    seen: dict = {}

    async def fake_auto_pair(backend, stream_url, *, kind, already_started=False, **_k):
        seen["already_started"] = already_started
        seen["kind"] = kind
        return AutoPairResult(
            success=True,
            reason="paired",
            pin="A1B2C3",
            pairing_started=True,
            hint="Paired successfully",
        )

    monkeypatch.setattr("apituner.main.auto_pair", fake_auto_pair)
    r = client.post(
        "/api/tuners/hybrid_remote/pair/auto",
        json={"already_started": True},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert seen == {"already_started": True, "kind": "androidtv_remote"}


def test_pair_auto_ocr_failed_returns_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "apituner.tuner_manager.TunerManager.get_pairing_backend",
        lambda self, tuner: _FakePairBackend(),
    )

    async def fake_auto_pair(*_a, **_k):
        return AutoPairResult(
            success=False,
            reason="ocr_failed",
            pairing_started=True,
            hint="Couldn't read the PIN",
        )

    monkeypatch.setattr("apituner.main.auto_pair", fake_auto_pair)
    r = client.post("/api/tuners/hybrid_remote/pair/auto", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is False
    assert data["reason"] == "ocr_failed"
    assert data["pairing_started"] is True


def test_pair_auto_persists_fire_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    class FakeFireBackend(_FakePairBackend):
        client_token = "fire-token-xyz"

    monkeypatch.setattr(
        "apituner.tuner_manager.TunerManager.get_pairing_backend",
        lambda self, tuner: FakeFireBackend(),
    )

    async def fake_auto_pair(backend, stream_url, *, kind, **_kwargs):
        assert kind == "firetv_rest"
        assert backend.client_token == "fire-token-xyz"
        return AutoPairResult(
            success=True,
            reason="paired",
            pin="4821",
            pairing_started=True,
            hint="Paired successfully",
        )

    monkeypatch.setattr("apituner.main.auto_pair", fake_auto_pair)
    r = client.post("/api/tuners/hybrid_fire/pair/auto", json={})
    assert r.status_code == 200
    assert r.json()["success"] is True

    store = ConfigStore(data_dir=tmp_path)
    hybrid = next(t for t in store.config.tuners if t.id == "hybrid_fire")
    assert hybrid.keys_control is not None
    assert hybrid.keys_control.token == "fire-token-xyz"


def test_grant_permissions_accepts_adb_tuner(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    seen: dict = {}

    async def fake_grant(host: str, *, adb_port: int = 5555) -> GrantResult:
        seen["host"] = host
        seen["adb_port"] = adb_port
        return GrantResult(
            overlay=True,
            usage=True,
            notification=True,
            accessibility=True,
            messages=["ok"],
        )

    monkeypatch.setattr("apituner.main.grant_agent_permissions", fake_grant)
    r = client.post("/api/tuners/adb1/grant-permissions", json={})
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert seen == {"host": "192.0.2.13", "adb_port": 5555}
