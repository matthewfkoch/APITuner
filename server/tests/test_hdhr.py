"""HDHomeRun protocol builders, discovery packets, and HTTP routes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from apituner.config import ConfigStore
from apituner.hdhr.discovery import (
    DiscoverIdentity,
    build_discover_response,
    parse_discover_request,
    wants_tuner,
)
from apituner.hdhr.lineup import (
    build_discover_json,
    build_lineup_json,
    find_channel,
)
from apituner.models import Channel, ControlConfig, GlobalOptions, Tuner
from apituner.tuner_manager import NoTunerAvailable, TunerInUse, TunerManager


def _channel(number: int, name: str = "Test") -> Channel:
    return Channel(number=number, name=name, package_name="com.example.app")


def _tuner(name: str, *, enabled: bool = True) -> Tuner:
    return Tuner(
        name=name,
        control=ControlConfig(type="http_agent", host="127.0.0.1", port=9092),
        stream_endpoint="http://127.0.0.1:8090/stream0",
        enabled=enabled,
    )


def test_build_discover_json_tuner_count():
    options = GlobalOptions(hdhr_device_id="AABBCCDD", hdhr_friendly_name="APITuner")
    data = build_discover_json(
        base_url="http://192.0.2.1:6592",
        options=options,
        tuner_count=3,
        firmware_version="0.1.4",
    )
    assert data["TunerCount"] == 3
    assert data["DeviceID"] == "AABBCCDD"
    assert data["LineupURL"] == "http://192.0.2.1:6592/lineup.json"
    assert data["ModelNumber"] == "HDTC-2US"


def test_build_lineup_json_auto_urls():
    channels = [_channel(36, "ESPN"), _channel(1, "ABC")]
    lineup = build_lineup_json(channels, "http://192.0.2.1:6592")
    assert [e["GuideNumber"] for e in lineup] == ["1", "36"]
    assert lineup[0]["GuideName"] == "ABC"
    assert lineup[0]["URL"] == "http://192.0.2.1:6592/auto/v1"
    assert lineup[1]["URL"] == "http://192.0.2.1:6592/auto/v36"


def test_find_channel_dotted_major():
    channels = [_channel(5, "NBC")]
    assert find_channel(channels, "5").number == 5
    assert find_channel(channels, "5.1").number == 5
    assert find_channel(channels, "999") is None


def test_discover_packet_roundtrip():
    identity = DiscoverIdentity(
        device_id_hex="AABBCCDD",
        tuner_count=2,
        base_url="http://192.0.2.10:6592",
    )
    frame = build_discover_response(identity)
    # Parse as if it were a request would fail (wrong type); verify CRC by roundtrip shape.
    assert len(frame) > 8
    assert frame[0:2] == b"\x00\x03"  # discover reply type

    # Build a minimal discover request and ensure filter helpers work.
    from apituner.hdhr import discovery as disc

    tags = [
        (disc._TAG_DEVICE_TYPE, disc._u32be(disc._DEVICE_TYPE_TUNER)),
        (disc._TAG_DEVICE_ID, disc._u32be(0xFFFFFFFF)),
    ]
    payload = bytearray()
    for tag, value in tags:
        payload.append(tag)
        disc._append_varlen(payload, len(value))
        payload.extend(value)
    req = bytearray(4 + len(payload) + 4)
    import struct
    import zlib

    struct.pack_into(">H", req, 0, disc._TYPE_DISCOVER_REQ)
    struct.pack_into(">H", req, 2, len(payload))
    req[4 : 4 + len(payload)] = payload
    crc = zlib.crc32(req[: 4 + len(payload)]) & 0xFFFFFFFF
    struct.pack_into("<I", req, 4 + len(payload), crc)

    parsed = parse_discover_request(bytes(req))
    assert parsed is not None
    device_types, device_id = parsed
    assert wants_tuner(device_types)
    assert device_id == 0xFFFFFFFF


def test_enabled_tuners_and_count(tmp_path: Path):
    store = ConfigStore(data_dir=tmp_path)
    store.config.tuners = [
        _tuner("A", enabled=True),
        _tuner("B", enabled=False),
        _tuner("C", enabled=True),
    ]
    store.save()
    manager = TunerManager(store)
    assert manager.tuner_count() == 2
    assert [t.name for t in manager.enabled_tuners()] == ["A", "C"]
    assert manager.tuner_at_index(0).name == "A"
    assert manager.tuner_at_index(1).name == "C"
    assert manager.tuner_at_index(2) is None


@pytest.mark.asyncio
async def test_lease_tuner_index_in_use(tmp_path: Path):
    store = ConfigStore(data_dir=tmp_path)
    store.config.tuners = [_tuner("A"), _tuner("B")]
    store.config.channels = [_channel(1)]
    store.config.options = GlobalOptions(
        wait_for_playback=False,
        tune_timeout_seconds=1.0,
        retry_on_other_tuner=False,
        hdhr_ssdp_enabled=False,
        hdhr_udp_discovery_enabled=False,
    )
    store.save()
    manager = TunerManager(store)
    channel = store.config.channels[0]

    # Lock tuner index 1 without going through full tune.
    locked = await manager._select_index(channel, 1)
    assert locked.name == "B"
    with pytest.raises(TunerInUse):
        await manager._select_index(channel, 1)
    with pytest.raises(NoTunerAvailable):
        await manager._select_index(channel, 99)


@pytest.fixture
def hdhr_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APITUNER_DATA_DIR", str(tmp_path))
    store = ConfigStore(data_dir=tmp_path)
    store.config.options.hdhr_enabled = True
    store.config.options.hdhr_device_id = "AABBCCDD"
    store.config.options.hdhr_friendly_name = "APITunerTest"
    store.config.options.hdhr_ssdp_enabled = False
    store.config.options.hdhr_udp_discovery_enabled = False
    store.config.tuners = [_tuner("A"), _tuner("B", enabled=False), _tuner("C")]
    store.config.channels = [_channel(9000, "ESPN"), _channel(1, "ABC")]
    store.save()

    # Import after env is set so lifespan uses the temp data dir.
    from apituner.main import app

    with TestClient(app) as client:
        yield client


def test_discover_json(hdhr_client: TestClient):
    resp = hdhr_client.get("/discover.json")
    assert resp.status_code == 200
    assert resp.headers.get("server") == "HDHomeRun/1.0"
    data = resp.json()
    assert data["TunerCount"] == 2  # only enabled tuners
    assert data["DeviceID"] == "AABBCCDD"
    assert data["FriendlyName"] == "APITunerTest"
    assert data["LineupURL"].endswith("/lineup.json")


def test_lineup_json_with_channels_query_params(hdhr_client: TestClient):
    resp = hdhr_client.get("/lineup.json?tuning&show=found")
    assert resp.status_code == 200
    lineup = resp.json()
    assert len(lineup) == 2
    assert lineup[0]["GuideNumber"] == "1"
    assert lineup[0]["URL"].endswith("/auto/v1")
    assert lineup[1]["GuideNumber"] == "9000"
    assert lineup[1]["URL"].endswith("/auto/v9000")


def test_lineup_status(hdhr_client: TestClient):
    resp = hdhr_client.get("/lineup_status.json")
    assert resp.status_code == 200
    assert resp.json()["ScanInProgress"] == 0


def test_auto_unknown_channel_801(hdhr_client: TestClient):
    resp = hdhr_client.get("/auto/v99999")
    assert resp.status_code == 404
    assert resp.headers.get("x-hdhomerun-error") == "801"


def test_tuner_index_route_uses_lease(hdhr_client: TestClient):
    """Ensure /tuner1/v{ch} passes tuner_index=1 into open_hdhr_stream."""
    called: dict = {}

    async def fake_open(request, manager, channel, *, tuner_index=None):
        called["channel"] = channel.number
        called["tuner_index"] = tuner_index
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse("ok", media_type="video/mp2t")

    with patch("apituner.hdhr.routes.open_hdhr_stream", new=fake_open):
        resp = hdhr_client.get("/tuner1/v9000")
    assert resp.status_code == 200
    assert called["channel"] == 9000
    assert called["tuner_index"] == 1


def test_api_status_includes_hdhr(hdhr_client: TestClient):
    resp = hdhr_client.get("/api/status")
    assert resp.status_code == 200
    hdhr = resp.json()["hdhr"]
    assert hdhr["enabled"] is True
    assert hdhr["tuner_count"] == 2
    assert hdhr["device_id"] == "AABBCCDD"
