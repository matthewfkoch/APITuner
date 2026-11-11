"""Tests for key_macro normalization."""

from __future__ import annotations

from apituner.keys import key_requires_dpad, normalize_key_macro, parse_key_macro_sleep
from apituner.models import Channel


def test_key_macro_split_semicolon_and_comma():
    assert normalize_key_macro("DPAD_CENTER;DPAD_CENTER") == [
        "DPAD_CENTER",
        "DPAD_CENTER",
    ]
    assert normalize_key_macro(["DPAD_CENTER;DPAD_DOWN", "HOME"]) == [
        "DPAD_CENTER",
        "DPAD_DOWN",
        "HOME",
    ]
    assert normalize_key_macro("KEYCODE_DPAD_CENTER, KEYCODE_DPAD_CENTER") == [
        "DPAD_CENTER",
        "DPAD_CENTER",
    ]


def test_channel_model_normalizes_key_macro():
    ch = Channel(
        number=3101,
        name="HBO",
        package_name="com.wbd.stream",
        url="https://play.hbomax.com/x",
        key_macro=["DPAD_CENTER;DPAD_CENTER"],
    )
    assert ch.key_macro == ["DPAD_CENTER", "DPAD_CENTER"]


def test_key_requires_dpad():
    assert not key_requires_dpad("BACK")
    assert not key_requires_dpad("HOME")
    assert not key_requires_dpad("SLEEP 4")
    assert not key_requires_dpad("sleep 0.5")
    assert key_requires_dpad("DPAD_CENTER")
    assert key_requires_dpad("MEDIA_STOP")


def test_key_macro_sleep_tokens():
    assert parse_key_macro_sleep("sleep 4") == 4.0
    assert parse_key_macro_sleep("SLEEP 0.5") == 0.5
    assert parse_key_macro_sleep("SLEEP_2.5") == 2.5
    assert parse_key_macro_sleep("WAIT 3") == 3.0
    assert parse_key_macro_sleep("DPAD_CENTER") is None
    assert normalize_key_macro("sleep 4, KEYCODE_DPAD_CENTER, sleep 0.5, DPAD_CENTER") == [
        "SLEEP 4",
        "DPAD_CENTER",
        "SLEEP 0.5",
        "DPAD_CENTER",
    ]



def test_decimal_macro_survives_config_reload(tmp_path):
    from apituner.config import ConfigStore

    store = ConfigStore(data_dir=tmp_path)
    store.import_channels([{
        "number": 1, "name": "Test", "package_name": "com.example",
        "key_macro": ["SLEEP 0.000001", "WAIT:1.500", "SLEEP", "DPAD_CENTER"],
    }])
    reloaded = ConfigStore(data_dir=tmp_path)
    assert reloaded.config.channels[0].key_macro == [
        "SLEEP 0.000001", "SLEEP 1.5", "SLEEP", "DPAD_CENTER",
    ]


def test_invalid_waits_are_rejected_by_channel_model():
    import pytest

    for macro in (
        "WAIT", "SLEEP -1", "SLEEP_foo", "WAIT 11", "SLEEP 8, WAIT 3",
        "SLEEP 10, WAIT 0.00000000000000000000000000001",
    ):
        with pytest.raises(ValueError, match="wait"):
            Channel(number=1, name="Test", package_name="com.example", key_macro=macro)
    Channel(number=1, name="Test", package_name="com.example", key_macro="WAIT 10")


async def test_legacy_invalid_wait_stops_tune_before_device_actions(tmp_path):
    import pytest
    from unittest.mock import AsyncMock

    from apituner.config import ConfigStore
    from apituner.models import ControlConfig, GlobalOptions, Tuner
    from apituner.tuner_manager import TuneFailed, TunerManager

    store = ConfigStore(data_dir=tmp_path)
    store.config.channels.append(Channel.model_construct(
        number="1", name="Test", package_name="com.example",
        key_macro=["DPAD_CENTER", "WAIT 11"],
    ))
    store.save()
    reloaded = ConfigStore(data_dir=tmp_path)
    channel = reloaded.config.channels[0]
    backend = AsyncMock()
    tuner = Tuner(
        id="test", name="Test", control=ControlConfig(host="192.0.2.1"),
        stream_endpoint="http://192.0.2.2/",
    )
    with pytest.raises(TuneFailed, match="at most 10 seconds"):
        await TunerManager(reloaded)._do_tune(tuner, backend, channel, "test", GlobalOptions())
    assert backend.mock_calls == []


async def test_wait_only_and_mixed_macros(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from apituner.backends.base import Capabilities
    from apituner.config import ConfigStore
    from apituner.tuner_manager import TunerManager

    events = []

    async def wait(seconds):
        events.append(("wait", seconds))

    async def send_key(key):
        events.append(("key", key))

    monkeypatch.setattr("apituner.tuner_manager.asyncio.sleep", wait)
    manager = TunerManager(ConfigStore(data_dir=tmp_path))
    capabilities = AsyncMock(return_value=Capabilities(dpad=True))
    monkeypatch.setattr(manager, "_effective_capabilities", capabilities)
    backend = AsyncMock()
    backend.send_key.side_effect = send_key
    channel = Channel(number=1, name="Test", package_name="com.example", key_macro="WAIT 0.000001")
    await manager._send_key_macro(channel, backend)
    capabilities.assert_not_awaited()
    assert events == [("wait", 0.000001)]

    events.clear()
    channel.key_macro = ["DPAD_CENTER", "WAIT 1.25", "DPAD_DOWN"]
    await manager._send_key_macro(channel, backend)
    assert events == [
        ("key", "DPAD_CENTER"), ("wait", 0.5), ("wait", 1.25),
        ("key", "DPAD_DOWN"), ("wait", 0.5),
    ]
