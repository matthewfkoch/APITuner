"""Tests for ADBTuner / babsonnexus configuration interpreter."""

from __future__ import annotations

import pytest

from apituner.backends.base import Capabilities, ControlBackend, PlaybackState
from apituner.config_interpreter import (
    ConfigInterpreterError,
    resolve_app_play_config,
    run_commands,
    url_looks_like_deeplink,
)
from apituner.models import Channel, TuneConfiguration


class RecordingBackend(ControlBackend):
    capabilities = Capabilities(keys=True, dpad=True)

    def __init__(self) -> None:
        self.launches: list[str] = []
        self.keys: list[str] = []
        self.stops = 0

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def health(self) -> bool:
        return True

    async def get_info(self):
        from apituner.backends.base import DeviceInfo

        return DeviceInfo()

    async def launch(self, *, package, deeplink=None, component=None, action=None, extras=None):
        self.launches.append(package)

    async def send_key(self, key: str) -> None:
        self.keys.append(key)

    async def current_app(self):
        return None

    async def playback_state(self) -> PlaybackState:
        return PlaybackState.UNKNOWN

    async def stop(self) -> None:
        self.stops += 1


def test_url_looks_like_deeplink():
    assert url_looks_like_deeplink("https://play.hbomax.com/x")
    assert url_looks_like_deeplink("nbctve://live/nbc")
    assert not url_looks_like_deeplink("0")
    assert not url_looks_like_deeplink("3")
    assert not url_looks_like_deeplink("")


def test_resolve_app_play_skips_deeplink_urls():
    cfg = TuneConfiguration(uuid="0AppPlay-espn", name="ESPN")
    ch = Channel(
        number=1501,
        name="ESPN",
        package_name="com.espn.score_center",
        url="0",
        configuration_uuid="0AppPlay-espn",
    )
    assert resolve_app_play_config(ch, [cfg]) is cfg

    hbo = Channel(
        number=3101,
        name="HBO",
        package_name="com.wbd.stream",
        url="https://play.hbomax.com/channel/watch/abc",
        configuration_uuid="92c0c532-aa12-4d18-abbd-72e4a9cec15c",
    )
    assert resolve_app_play_config(hbo, [cfg]) is None


def test_resolve_app_play_missing_config_raises():
    ch = Channel(
        number=1501,
        name="ESPN",
        package_name="com.espn.score_center",
        url="0",
        configuration_uuid="0AppPlay-missing",
    )
    with pytest.raises(ConfigInterpreterError, match="not imported"):
        resolve_app_play_config(ch, [])


@pytest.mark.asyncio
async def test_run_commands_espn_style():
    backend = RecordingBackend()
    commands = [
        "input keyevent KEYCODE_MEDIA_STOP",
        "am force-stop '||TARGET_PACKAGE_NAME||'",
        "adbtuner_open_app '||TARGET_PACKAGE_NAME||'",
        "sleep 0.01",
        {
            "ADB_LOOP": {
                "iterations": "||TARGET_URL_OR_IDENTIFIER||",
                "commands": [
                    "input keyevent KEYCODE_DPAD_RIGHT",
                    "sleep 0.01",
                ],
            }
        },
        "input keyevent KEYCODE_DPAD_CENTER",
    ]
    await run_commands(
        backend, commands, package="com.espn.score_center", identifier="2"
    )
    assert backend.stops == 1
    assert backend.launches == ["com.espn.score_center"]
    assert backend.keys == [
        "MEDIA_STOP",
        "DPAD_RIGHT",
        "DPAD_RIGHT",
        "DPAD_CENTER",
    ]


@pytest.mark.asyncio
async def test_unknown_command_raises():
    backend = RecordingBackend()
    with pytest.raises(ConfigInterpreterError, match="Unsupported"):
        await run_commands(
            backend, ["dumpsys activity"], package="com.a", identifier="0"
        )


def test_import_configuration_and_channel_uuid(tmp_path):
    from apituner.config import ConfigStore

    store = ConfigStore(data_dir=tmp_path)
    count = store.import_configurations(
        {
            "uuid": "0AppPlay-1500-0000-0000-ESPN00000000",
            "name": "App Play - ESPN",
            "global_options": {
                "use_fixed_delay": True,
                "fixed_delay_seconds": 1,
            },
            "tune_commands": ["adbtuner_open_app '||TARGET_PACKAGE_NAME||'"],
        }
    )
    assert count == 1
    assert store.config.configurations[0].uuid.startswith("0AppPlay")

    store.import_channels(
        [
            {
                "provider_name": "app_espn",
                "number": 1501,
                "name": "ESPN",
                "url": "0",
                "package_name": "com.espn.score_center",
                "configuration_uuid": "0AppPlay-1500-0000-0000-ESPN00000000",
                "tvc_guide_stationid": "32645",
            }
        ],
        replace=True,
    )
    ch = store.config.channels[0]
    assert ch.configuration_uuid == "0AppPlay-1500-0000-0000-ESPN00000000"
    exported = store.export_channels()
    assert exported[0]["configuration_uuid"] == ch.configuration_uuid
