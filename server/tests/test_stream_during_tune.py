"""stream_during_tune returns a lease before App Play finishes."""

from __future__ import annotations

import asyncio

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
from apituner.stream import _tune_failed
from apituner.tuner_manager import Lease, TunerManager


class SlowDpadBackend(ControlBackend):
    capabilities = Capabilities(keys=True, dpad=True)

    def __init__(self, *, hold: asyncio.Event) -> None:
        self.hold = hold
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

        return DeviceInfo(packages=["com.espn.score_center"])

    async def launch(self, *, package, deeplink=None, component=None, action=None, extras=None):
        self.launches.append(package)

    async def send_key(self, key: str) -> None:
        self.keys.append(key)
        if key == "DPAD_CENTER":
            await self.hold.wait()

    async def current_app(self):
        return None

    async def playback_state(self) -> PlaybackState:
        return PlaybackState.UNKNOWN

    async def stop(self) -> None:
        return None


@pytest.mark.asyncio
async def test_stream_during_tune_returns_before_app_play_finishes(tmp_path):
    hold = asyncio.Event()
    store = ConfigStore(data_dir=tmp_path)
    store.config.options = GlobalOptions(stream_during_tune=True)
    store.config.configurations = [
        TuneConfiguration(
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
    ]
    store.config.tuners = [
        Tuner(
            id="t1",
            name="Remote",
            control=ControlConfig(type="androidtv_remote", host="192.0.2.1"),
            stream_endpoint="http://192.0.2.2/s",
        )
    ]
    store.config.channels = [
        Channel(
            number=1507,
            name="ESPN Deportes",
            package_name="com.espn.score_center",
            url="0",
            configuration_uuid="0AppPlay-espn",
        )
    ]
    store.save()

    manager = TunerManager(store)
    backend = SlowDpadBackend(hold=hold)
    manager._backends["t1"] = backend

    lease_task = asyncio.create_task(manager.lease(store.config.channels[0]))
    lease = await asyncio.wait_for(lease_task, timeout=2.0)
    assert isinstance(lease, Lease)
    assert lease.tune_task is not None
    assert not lease.tune_task.done()
    assert manager.status()[0]["channel_number"] == 1507
    assert manager.status()[0]["locked"] is True

    hold.set()
    await asyncio.wait_for(lease.tune_task, timeout=2.0)
    assert lease.tune_task.exception() is None
    assert backend.launches == ["com.espn.score_center"]
    assert "DPAD_CENTER" in backend.keys

    await manager.release(lease)
    assert manager.status()[0]["locked"] is False


@pytest.mark.asyncio
async def test_stream_during_tune_off_awaits_ready(tmp_path):
    hold = asyncio.Event()
    store = ConfigStore(data_dir=tmp_path)
    store.config.options = GlobalOptions(stream_during_tune=False)
    store.config.configurations = [
        TuneConfiguration(
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
    ]
    store.config.tuners = [
        Tuner(
            id="t1",
            name="Remote",
            control=ControlConfig(type="androidtv_remote", host="192.0.2.1"),
            stream_endpoint="http://192.0.2.2/s",
        )
    ]
    store.config.channels = [
        Channel(
            number=1507,
            name="ESPN Deportes",
            package_name="com.espn.score_center",
            url="0",
            configuration_uuid="0AppPlay-espn",
        )
    ]
    store.save()

    manager = TunerManager(store)
    backend = SlowDpadBackend(hold=hold)
    manager._backends["t1"] = backend

    lease_task = asyncio.create_task(manager.lease(store.config.channels[0]))
    await asyncio.sleep(0.05)
    assert not lease_task.done()
    hold.set()
    lease = await asyncio.wait_for(lease_task, timeout=2.0)
    assert lease.tune_task is None
    await manager.release(lease)


@pytest.mark.asyncio
async def test_tune_failed_helper():
    async def _boom():
        raise RuntimeError("nope")

    async def _ok():
        return None

    tuner = Tuner(
        id="t",
        name="t",
        control=ControlConfig(type="adb", host="192.0.2.1"),
        stream_endpoint="http://192.0.2.2/s",
    )
    channel = Channel(number=1, name="X", package_name="p")
    backend = SlowDpadBackend(hold=asyncio.Event())

    bad = asyncio.create_task(_boom())
    with pytest.raises(RuntimeError):
        await bad
    lease = Lease(
        tuner=tuner,
        backend=backend,
        tune_id="abc",
        channel=channel,
        tune_task=bad,
    )
    assert isinstance(_tune_failed(lease), RuntimeError)

    good_task = asyncio.create_task(_ok())
    await good_task
    lease.tune_task = good_task
    assert _tune_failed(lease) is None

    lease.tune_task = None
    assert _tune_failed(lease) is None
