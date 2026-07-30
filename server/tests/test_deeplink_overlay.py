"""Deep-link config overlay + hybrid keys_control behavior."""

from __future__ import annotations

from typing import Optional

import pytest

from apituner.backends.base import Capabilities, ControlBackend, PlaybackState
from apituner.backends.hybrid import SplitControlBackend
from apituner.config import ConfigStore
from apituner.config_interpreter import (
    resolve_app_play_config,
    resolve_tune_configuration,
)
from apituner.models import (
    Channel,
    ControlConfig,
    GlobalOptions,
    TuneConfiguration,
    TuneConfigurationOptions,
    Tuner,
)
from apituner.tuner_manager import TuneFailed, TunerManager


class RecordingBackend(ControlBackend):
    def __init__(self, *, dpad: bool = False, keys: bool = True) -> None:
        self.capabilities = Capabilities(
            keys=keys, dpad=dpad, current_app=True, playback_state=True
        )
        self.launches: list[tuple] = []
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
        self.launches.append((package, deeplink))

    async def send_key(self, key: str) -> None:
        self.keys.append(key)

    async def current_app(self) -> Optional[str]:
        return "com.wbd.stream"

    async def playback_state(self) -> PlaybackState:
        return PlaybackState.PLAYING

    async def stop(self) -> None:
        self.stops += 1


COMPAT_UUID = "c513c18d-19bd-47f0-9bff-4baba2a8c4cd"


def _compat_cfg() -> TuneConfiguration:
    return TuneConfiguration(
        uuid=COMPAT_UUID,
        name="Deep Links - Compatibility Mode",
        global_options=TuneConfigurationOptions(
            check_for_and_clear_whos_watching_prompts=True,
            use_fixed_delay=True,
            fixed_delay_seconds=0.01,
            wait_for_video_playback_detection=False,
        ),
        pre_tune_commands=[
            "input keyevent KEYCODE_MEDIA_STOP",
            "am force-stop '||TARGET_PACKAGE_NAME||'",
        ],
        tune_commands=[
            "am start -W -a android.intent.action.VIEW -d '||TARGET_URL_OR_IDENTIFIER||' '||TARGET_PACKAGE_NAME||'"
        ],
        post_tune_commands=["input keyevent KEYCODE_HOME"],
    )


def test_resolve_overlay_vs_app_play():
    cfg = _compat_cfg()
    hbo = Channel(
        number=3101,
        name="HBO",
        package_name="com.wbd.stream",
        url="https://play.hbomax.com/channel/watch/abc",
        configuration_uuid=COMPAT_UUID,
        key_macro=["DPAD_CENTER;DPAD_CENTER"],
    )
    assert resolve_app_play_config(hbo, [cfg]) is None
    assert resolve_tune_configuration(hbo, [cfg]) is cfg

    espn = Channel(
        number=1501,
        name="ESPN",
        package_name="com.espn.score_center",
        url="0",
        configuration_uuid="0AppPlay-espn",
    )
    app = TuneConfiguration(uuid="0AppPlay-espn", name="ESPN")
    assert resolve_app_play_config(espn, [app]) is app


@pytest.mark.asyncio
async def test_deeplink_overlay_sends_key_macro_on_keys_backend(tmp_path, monkeypatch):
    store = ConfigStore(data_dir=tmp_path)
    store.config.configurations = [_compat_cfg()]
    store.save()
    manager = TunerManager(store)

    agent = RecordingBackend(dpad=False)
    remote = RecordingBackend(dpad=True)
    split = SplitControlBackend(agent, remote)

    async def no_whos(**kwargs):
        return "ocr_absent"

    monkeypatch.setattr(
        "apituner.tuner_manager.clear_whos_watching_prompt", no_whos
    )

    tuner = Tuner(
        id="t1",
        name="onn",
        control=ControlConfig(type="http_agent", host="192.0.2.1"),
        keys_control=ControlConfig(type="androidtv_remote", host="192.0.2.1"),
        stream_endpoint="http://192.0.2.2/s",
    )
    channel = Channel(
        number=3101,
        name="HBO",
        package_name="com.wbd.stream",
        url="https://play.hbomax.com/channel/watch/abc",
        configuration_uuid=COMPAT_UUID,
        compatibility_mode=True,
        key_macro=["DPAD_CENTER;DPAD_CENTER"],
    )

    await manager._do_tune(
        tuner,
        agent,
        channel,
        "tune1",
        GlobalOptions(wait_for_playback=True, ready_settle_seconds=0),
        app_play=None,
        overlay=store.config.configurations[0],
        command_backend=split,
    )

    assert agent.launches == [("com.wbd.stream", "https://play.hbomax.com/channel/watch/abc")]
    assert remote.keys == ["MEDIA_STOP", "DPAD_CENTER", "DPAD_CENTER"]
    assert agent.keys == []


@pytest.mark.asyncio
async def test_key_macro_fails_without_dpad(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    agent = RecordingBackend(dpad=False)
    tuner = Tuner(
        id="t1",
        name="onn",
        control=ControlConfig(type="http_agent", host="192.0.2.1"),
        stream_endpoint="http://192.0.2.2/s",
    )
    channel = Channel(
        number=3101,
        name="HBO",
        package_name="com.wbd.stream",
        url="https://play.hbomax.com/x",
        key_macro=["DPAD_CENTER"],
    )
    with pytest.raises(TuneFailed, match="D-pad"):
        await manager._do_tune(
            tuner,
            agent,
            channel,
            "tune1",
            GlobalOptions(wait_for_playback=True, ready_settle_seconds=0),
        )


@pytest.mark.asyncio
async def test_hybrid_split_capabilities():
    agent = RecordingBackend(dpad=False)
    remote = RecordingBackend(dpad=True)
    split = SplitControlBackend(agent, remote)
    assert split.capabilities.dpad is True
    assert split.capabilities.current_app is True
    await split.send_key("DPAD_CENTER")
    assert remote.keys == ["DPAD_CENTER"]
    await split.launch(package="com.x", deeplink="https://example.com")
    assert agent.launches == [("com.x", "https://example.com")]


@pytest.mark.asyncio
async def test_app_play_uses_keys_backend(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    cfg = TuneConfiguration(
        uuid="0AppPlay-espn",
        name="ESPN",
        global_options=TuneConfigurationOptions(
            use_fixed_delay=True,
            fixed_delay_seconds=0.01,
            check_for_and_clear_whos_watching_prompts=False,
        ),
        tune_commands=[
            "adbtuner_open_app '||TARGET_PACKAGE_NAME||'",
            "input keyevent KEYCODE_DPAD_CENTER",
        ],
    )
    store.config.configurations = [cfg]
    store.save()
    manager = TunerManager(store)
    agent = RecordingBackend(dpad=False)
    remote = RecordingBackend(dpad=True)
    split = SplitControlBackend(agent, remote)
    tuner = Tuner(
        id="t1",
        name="onn",
        control=ControlConfig(type="http_agent", host="192.0.2.1"),
        keys_control=ControlConfig(type="androidtv_remote", host="192.0.2.1"),
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
        agent,
        channel,
        "tune1",
        GlobalOptions(),
        app_play=cfg,
        command_backend=split,
    )
    assert agent.launches == [("com.espn.score_center", None)]
    assert remote.keys == ["DPAD_CENTER"]
