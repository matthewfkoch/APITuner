"""App Play tune path requires a D-pad backend."""

from __future__ import annotations

import pytest

from apituner.backends.base import Capabilities, ControlBackend, PlaybackState
from apituner.config import ConfigStore
from apituner.models import (
    Channel,
    ControlConfig,
    GlobalOptions,
    TuneConfiguration,
    TuneConfigurationOptions,
    Tuner,
)
from apituner.tuner_manager import TuneFailed, TunerManager


class DpadBackend(ControlBackend):
    capabilities = Capabilities(keys=True, dpad=True)

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.launches: list[str] = []

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
        return None


class AgentLikeBackend(DpadBackend):
    capabilities = Capabilities(keys=True, dpad=False)


@pytest.mark.asyncio
async def test_app_play_rejects_non_dpad_backend(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    store.config.configurations = [
        TuneConfiguration(
            uuid="0AppPlay-espn",
            name="ESPN",
            global_options=TuneConfigurationOptions(
                use_fixed_delay=True, fixed_delay_seconds=0.01
            ),
            tune_commands=["adbtuner_open_app '||TARGET_PACKAGE_NAME||'"],
        )
    ]
    store.save()
    manager = TunerManager(store)
    backend = AgentLikeBackend()
    tuner = Tuner(
        id="t1",
        name="Agent",
        control=ControlConfig(type="http_agent", host="192.0.2.1"),
        stream_endpoint="http://192.0.2.2/s",
    )
    channel = Channel(
        number=1501,
        name="ESPN",
        package_name="com.espn.score_center",
        url="0",
        configuration_uuid="0AppPlay-espn",
    )
    with pytest.raises(TuneFailed, match="D-pad"):
        await manager._do_tune(
            tuner,
            backend,
            channel,
            "tune1",
            GlobalOptions(),
            app_play=store.config.configurations[0],
        )


@pytest.mark.asyncio
async def test_app_play_runs_commands_on_dpad_backend(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    cfg = TuneConfiguration(
        uuid="0AppPlay-espn",
        name="ESPN",
        global_options=TuneConfigurationOptions(
            use_fixed_delay=True,
            fixed_delay_seconds=0.01,
            check_for_and_clear_whos_watching_prompts=False,
        ),
        pre_tune_commands=[],
        tune_commands=[
            "adbtuner_open_app '||TARGET_PACKAGE_NAME||'",
            "input keyevent KEYCODE_DPAD_CENTER",
        ],
    )
    store.config.configurations = [cfg]
    store.save()
    manager = TunerManager(store)
    backend = DpadBackend()
    tuner = Tuner(
        id="t1",
        name="Remote",
        control=ControlConfig(type="androidtv_remote", host="192.0.2.1"),
        stream_endpoint="http://192.0.2.2/s",
    )
    channel = Channel(
        number=1501,
        name="ESPN",
        package_name="com.espn.score_center",
        url="0",
        configuration_uuid="0AppPlay-espn",
    )
    await manager._do_tune(
        tuner,
        backend,
        channel,
        "tune1",
        GlobalOptions(),
        app_play=cfg,
    )
    assert backend.launches == ["com.espn.score_center"]
    # Wake + HOME precede the config script (dismiss screensaver / ambient).
    assert backend.keys[:2] == ["WAKEUP", "HOME"]
    assert backend.keys[-1] == "DPAD_CENTER"
