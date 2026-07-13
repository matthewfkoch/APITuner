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

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def health(self) -> bool:
        return True

    async def get_info(self):
        from apituner.backends.base import DeviceInfo

        return DeviceInfo(packages=["com.google.android.youtube.tvunplugged"])

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
async def test_wait_ready_same_app_switch(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.google.android.youtube.tvunplugged"

    channel = Channel(
        number=36,
        name="ESPN",
        package_name="com.google.android.youtube.tvunplugged",
        url="https://tv.youtube.com/watch/example",
    )
    options = GlobalOptions(wait_for_playback=True, tune_timeout_seconds=5.0)
    launch_at = time.monotonic()
    deadline = launch_at + options.tune_timeout_seconds

    ready = await manager._wait_ready(
        backend,
        channel,
        "com.google.android.youtube.tvunplugged",
        options,
        deadline,
        prior_app="com.google.android.youtube.tvunplugged",
        launch_at=launch_at,
    )
    assert ready is True


@pytest.mark.asyncio
async def test_wait_ready_accepts_matching_foreground(tmp_path):
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
    options = GlobalOptions(wait_for_playback=True, tune_timeout_seconds=10.0)
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
