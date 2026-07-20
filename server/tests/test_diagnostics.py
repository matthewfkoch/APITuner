"""Diagnostics download: log ring buffer + redacted tuner probes."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apituner.config import ConfigStore
from apituner.log_buffer import RingBufferHandler, install_log_buffer
from apituner.models import ControlConfig, Tuner


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APITUNER_DATA_DIR", str(tmp_path))
    store = ConfigStore(data_dir=tmp_path)
    store.config.options.hdhr_enabled = False
    store.config.options.hdhr_ssdp_enabled = False
    store.config.options.hdhr_udp_discovery_enabled = False
    store.config.tuners = [
        Tuner(
            id="diagtest",
            name="Diag Tuner",
            control=ControlConfig(
                type="http_agent",
                host="192.0.2.50",
                port=9092,
                token="super-secret-token",
            ),
            stream_endpoint="http://192.0.2.60/0.ts",
            enabled=True,
        )
    ]
    store.save()

    install_log_buffer()
    logging.getLogger("apituner").info("diagnostics-test-line")

    from apituner.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_ring_buffer_captures_lines():
    handler = RingBufferHandler(capacity=10)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger("apituner.test.ring")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    log.info("hello-ring")
    assert any("hello-ring" in line for line in handler.lines())
    log.removeHandler(handler)


def test_diagnostics_download_redacts_token(client: TestClient):
    resp = client.get("/api/diagnostics")
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("content-disposition", "")
    data = resp.json()
    assert data["apituner_version"]
    assert data["channel_count"] == 0
    assert data["tuner_count"] == 1
    tuner = data["tuners"][0]
    assert tuner["control"]["token"] == "***"
    assert "super-secret-token" not in resp.text
    assert tuner["stream_endpoint"] == "http://192.0.2.60/0.ts"
    # Unreachable Agent must not fail the bundle.
    assert "probe" in tuner
    assert isinstance(data["recent_logs"], list)
