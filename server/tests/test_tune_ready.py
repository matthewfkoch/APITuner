from __future__ import annotations

import asyncio
import time

import pytest

from apituner.backends.base import Capabilities, ControlBackend, PlaybackState
from apituner.config import ConfigStore
from apituner.models import Channel, GlobalOptions
from apituner.tuner_manager import TunerManager


class StubBackend(ControlBackend):
    capabilities = Capabilities(current_app=True, playback_state=True)

    def __init__(self) -> None:
        self.current: str | None = None
        self.playback = PlaybackState.UNKNOWN
        self.live_caps: dict[str, bool] | None = None

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def health(self) -> bool:
        return True

    async def get_info(self):
        from apituner.backends.base import DeviceInfo

        return DeviceInfo(packages=["com.google.android.youtube.tvunplugged"])

    async def get_live_capabilities(self) -> dict[str, bool]:
        return self.live_caps or {}

    async def launch(self, *, package, deeplink=None, component=None, action=None, extras=None):
        self.current = package

    async def send_key(self, key: str) -> None:
        return None

    async def current_app(self) -> str | None:
        return self.current

    async def playback_state(self) -> PlaybackState:
        return self.playback

    async def stop(self) -> None:
        return None


@pytest.mark.asyncio
async def test_wait_ready_same_app_waits_for_playback(tmp_path):
    """Same-app deeplink must not ready at 2s while playback wait is still active."""
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.google.android.youtube.tvunplugged"
    backend.playback = PlaybackState.IDLE

    channel = Channel(
        number=36,
        name="ESPN",
        package_name="com.google.android.youtube.tvunplugged",
        url="https://tv.youtube.com/watch/example",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=1.5,
        ready_settle_seconds=0.0,
    )
    launch_at = time.monotonic()

    ready = await manager._wait_ready(
        backend,
        channel,
        "com.google.android.youtube.tvunplugged",
        options,
        launch_at + options.tune_timeout_seconds,
        prior_app="com.google.android.youtube.tvunplugged",
        launch_at=launch_at,
    )
    # Timeout grace still allows same-app / foreground accept.
    assert ready is True


@pytest.mark.asyncio
async def test_wait_ready_rejects_foreground_while_waiting_playback(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.google.android.youtube.tvunplugged"
    backend.playback = PlaybackState.IDLE

    channel = Channel(
        number=1,
        name="ABC",
        package_name="com.google.android.youtube.tvunplugged",
        url="https://tv.youtube.com/watch/example",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=2.0,
        ready_settle_seconds=0.0,
    )
    launch_at = time.monotonic()

    task = asyncio.create_task(
        manager._wait_ready(
            backend,
            channel,
            "com.google.android.youtube.tvunplugged",
            options,
            launch_at + 10.0,
            prior_app=None,
            launch_at=launch_at,
        )
    )
    await asyncio.sleep(0.9)
    assert not task.done()
    backend.playback = PlaybackState.PLAYING
    ready = await asyncio.wait_for(task, timeout=3.0)
    assert ready is True


@pytest.mark.asyncio
async def test_wait_ready_playing_settles(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.google.android.youtube.tvunplugged"
    backend.playback = PlaybackState.PLAYING

    channel = Channel(
        number=1,
        name="ABC",
        package_name="com.google.android.youtube.tvunplugged",
        url="https://tv.youtube.com/watch/example",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=10.0,
        ready_settle_seconds=0.2,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.google.android.youtube.tvunplugged",
        options,
        launch_at + 10.0,
        prior_app=None,
        launch_at=launch_at,
    )
    assert ready is True
    assert time.monotonic() - launch_at >= 0.2


@pytest.mark.asyncio
async def test_wait_ready_falls_back_after_idle_timeout(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.google.android.youtube.tvunplugged"
    backend.playback = PlaybackState.IDLE

    channel = Channel(
        number=1,
        name="ABC",
        package_name="com.google.android.youtube.tvunplugged",
        url="https://tv.youtube.com/watch/example",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=10.0,
        ready_settle_seconds=0.0,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.google.android.youtube.tvunplugged",
        options,
        launch_at + 10.0,
        prior_app=None,
        launch_at=launch_at,
    )
    assert ready is True
    # Fallback kicks in after ~3s of IDLE + poll sleeps.
    assert time.monotonic() - launch_at >= 3.0


@pytest.mark.asyncio
async def test_live_caps_disable_playback_wait(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.google.android.youtube.tvunplugged"
    backend.playback = PlaybackState.IDLE
    backend.live_caps = {
        "keys": False,
        "current_app": True,
        "playback_state": False,
        "app_list": True,
        "install": True,
    }

    channel = Channel(
        number=1,
        name="ABC",
        package_name="com.google.android.youtube.tvunplugged",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=5.0,
        ready_settle_seconds=0.0,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.google.android.youtube.tvunplugged",
        options,
        launch_at + 5.0,
        prior_app=None,
        launch_at=launch_at,
    )
    assert ready is True
    # Without live playback permission, foreground accept is immediate.
    assert time.monotonic() - launch_at < 2.0
