from __future__ import annotations

import asyncio
import time

import pytest

from apituner.backends.base import Capabilities, ControlBackend, PlaybackState
from apituner.config import ConfigStore
from apituner.models import Channel, GlobalOptions
from apituner.tuner_manager import TunerManager, tune_not_ready_message


def test_tune_not_ready_message_points_at_the_cause():
    idle = tune_not_ready_message(
        number="77",
        name="MeTV",
        playback=PlaybackState.IDLE,
        title=None,
        relaunches=2,
        splash_extended=True,
    )
    assert "playback=IDLE" in idle
    assert "Notification access is working" in idle

    unknown = tune_not_ready_message(
        number="77",
        name="MeTV",
        playback=PlaybackState.UNKNOWN,
        title=None,
        relaunches=0,
    )
    assert "Notification access" in unknown

    verb = tune_not_ready_message(
        number="77",
        name="MeTV",
        playback=PlaybackState.IDLE,
        title=None,
        relaunches=2,
        relaunch_error="HTTP verb {}POST unhandled",
    )
    assert "Android home screen" not in verb
    assert "malformed HTTP verb" in verb
    assert "Update the APITuner server" in verb


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

    async def playback_snapshot(self):
        return self.playback, self.current, getattr(self, "title", None)

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


def test_title_looks_like_channel():
    from apituner.tuner_manager import _title_looks_like_channel, _titles_equivalent

    assert _title_looks_like_channel("YES Network HD", "NBC-WNBC") is False
    assert _title_looks_like_channel("WNBC", "NBC-WNBC") is True
    assert _title_looks_like_channel("", "NBC-WNBC") is None
    assert _title_looks_like_channel("Live TV", "NBC-WNBC") is None
    # DirecTV reports program titles, not call signs (community dump 0.1.25).
    assert _title_looks_like_channel("The Golden Girls", "MeTV") is False
    assert _title_looks_like_channel("Riders of the Purple Sage", "GRIT") is False
    assert _title_looks_like_channel("Sherlock on Masterpiece", "PBS-WEDW") is False
    assert _title_looks_like_channel("Unknown Title", "Cozi TV") is None
    assert _titles_equivalent("YES Network HD", "YES")
    assert _titles_equivalent("Chicago Fire", "Chicago Fire")
    assert not _titles_equivalent("Chicago Fire", "The Golden Girls")
    assert not _titles_equivalent("Sherlock on Masterpiece", "Riders of the Purple Sage")


@pytest.mark.asyncio
async def test_stale_directv_playing_does_not_ready_immediately(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.PLAYING
    backend.title = "YES Network HD"

    channel = Channel(
        number=4,
        name="NBC-WNBC",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/4",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=2.0,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=5.0,
    )
    launch_at = time.monotonic()
    task = asyncio.create_task(
        manager._wait_ready(
            backend,
            channel,
            "com.att.tv",
            options,
            launch_at + 10.0,
            prior_app="com.att.tv",
            launch_at=launch_at,
        )
    )
    await asyncio.sleep(0.9)
    assert not task.done()
    backend.playback = PlaybackState.IDLE
    backend.title = None
    await asyncio.sleep(0.9)
    backend.playback = PlaybackState.PLAYING
    backend.title = "WNBC"
    ready = await asyncio.wait_for(task, timeout=3.0)
    assert ready is True
    assert time.monotonic() - launch_at >= 1.5


@pytest.mark.asyncio
async def test_directv_wrong_title_does_not_ready(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.PLAYING
    backend.title = "YES Network HD"

    channel = Channel(
        number=4,
        name="NBC-WNBC",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/4",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=1.2,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=0.3,
    )
    relaunches = 0

    async def _relaunch() -> None:
        nonlocal relaunches
        relaunches += 1

    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.att.tv",
        options,
        launch_at + options.tune_timeout_seconds,
        prior_app="com.att.tv",
        launch_at=launch_at,
        relaunch=_relaunch,
    )
    assert ready is False
    assert relaunches == 2


@pytest.mark.asyncio
async def test_directv_program_title_change_is_ready(tmp_path):
    """techpro2004 dump: leftover Chicago Fire, then Golden Girls while tuning MeTV."""
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.PLAYING
    backend.title = "Chicago Fire"

    channel = Channel(
        number=77,
        name="MeTV",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/77",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=3.0,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=5.0,
    )
    launch_at = time.monotonic()
    task = asyncio.create_task(
        manager._wait_ready(
            backend,
            channel,
            "com.att.tv",
            options,
            launch_at + 10.0,
            prior_app="com.att.tv",
            launch_at=launch_at,
            leftover_title="Chicago Fire",
        )
    )
    await asyncio.sleep(0.4)
    assert not task.done()
    backend.title = "The Golden Girls"
    ready = await asyncio.wait_for(task, timeout=3.0)
    assert ready is True


@pytest.mark.asyncio
async def test_directv_pbs_and_grit_title_changes_are_ready(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.PLAYING
    backend.title = "Joffrey Ballet: The Next Movement"

    pbs = Channel(
        number=49,
        name="PBS-WEDW",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/49",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=3.0,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=5.0,
    )
    launch_at = time.monotonic()
    task = asyncio.create_task(
        manager._wait_ready(
            backend,
            pbs,
            "com.att.tv",
            options,
            launch_at + 10.0,
            prior_app="com.att.tv",
            launch_at=launch_at,
            leftover_title="Joffrey Ballet: The Next Movement",
        )
    )
    await asyncio.sleep(0.3)
    backend.title = "Sherlock on Masterpiece"
    assert await asyncio.wait_for(task, timeout=3.0) is True

    backend.title = "Sherlock on Masterpiece"
    grit = Channel(
        number=81,
        name="GRIT",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/81",
    )
    launch_at = time.monotonic()
    task = asyncio.create_task(
        manager._wait_ready(
            backend,
            grit,
            "com.att.tv",
            options,
            launch_at + 10.0,
            prior_app="com.att.tv",
            launch_at=launch_at,
            leftover_title="Sherlock on Masterpiece",
        )
    )
    await asyncio.sleep(0.3)
    backend.title = "Riders of the Purple Sage"
    assert await asyncio.wait_for(task, timeout=3.0) is True


@pytest.mark.asyncio
async def test_directv_idle_then_continue_watching_second_relaunch(tmp_path):
    """First-boot dump: idle relaunch, Roseanne leftover, second relaunch, then GRIT."""
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.IDLE
    relaunches = 0

    async def _relaunch() -> None:
        nonlocal relaunches
        relaunches += 1
        backend.playback = PlaybackState.PLAYING
        if relaunches == 1:
            backend.title = "Roseanne"
        else:
            backend.title = "Riders of the Purple Sage"

    channel = Channel(
        number=81,
        name="GRIT",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/81",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=10.0,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=0.3,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.att.tv",
        options,
        launch_at + 10.0,
        prior_app=None,
        launch_at=launch_at,
        relaunch=_relaunch,
        leftover_title=None,
    )
    assert ready is True
    assert relaunches == 2


@pytest.mark.asyncio
async def test_yttv_still_relaunches_once(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.google.android.youtube.tvunplugged"
    backend.playback = PlaybackState.IDLE
    relaunches = 0

    async def _relaunch() -> None:
        nonlocal relaunches
        relaunches += 1

    channel = Channel(
        number=36,
        name="ESPN",
        package_name="com.google.android.youtube.tvunplugged",
        url="https://tv.youtube.com/watch/example",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=1.2,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=0.3,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.google.android.youtube.tvunplugged",
        options,
        launch_at + options.tune_timeout_seconds,
        prior_app=None,
        launch_at=launch_at,
        relaunch=_relaunch,
    )
    assert ready is False
    assert relaunches == 1


@pytest.mark.asyncio
async def test_directv_unknown_title_then_show_is_ready(tmp_path):
    """First-boot dump: Unknown Title splash, then Cozi's actual program."""
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.PLAYING
    backend.title = "Unknown Title"
    relaunches = 0

    async def _relaunch() -> None:
        nonlocal relaunches
        relaunches += 1

    channel = Channel(
        number=100,
        name="Cozi TV",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/100",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=10.0,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=5.0,
    )
    launch_at = time.monotonic()
    task = asyncio.create_task(
        manager._wait_ready(
            backend,
            channel,
            "com.att.tv",
            options,
            launch_at + 10.0,
            prior_app=None,
            launch_at=launch_at,
            relaunch=_relaunch,
            leftover_title=None,
        )
    )
    await asyncio.sleep(0.4)
    assert not task.done()
    backend.title = "Funny You Should Ask"
    assert await asyncio.wait_for(task, timeout=3.0) is True
    assert relaunches == 0


@pytest.mark.asyncio
async def test_directv_unknown_title_does_not_fast_relaunch(tmp_path):
    """Cold-start 2s retry must not fire on splash titles (Cozi succeeded at ~3s)."""
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.PLAYING
    backend.title = "Unknown Title"
    relaunches = 0

    async def _relaunch() -> None:
        nonlocal relaunches
        relaunches += 1

    channel = Channel(
        number=100,
        name="Cozi TV",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/100",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=3.0,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=5.0,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.att.tv",
        options,
        launch_at + 3.0,
        prior_app=None,
        launch_at=launch_at,
        relaunch=_relaunch,
    )
    assert ready is False
    assert relaunches == 0


@pytest.mark.asyncio
async def test_directv_stuck_leftover_gets_second_relaunch(tmp_path):
    """First-boot dump: Red Tomahawk stuck on MeTV until a second relaunch."""
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.PLAYING
    backend.title = "Red Tomahawk"
    relaunches = 0

    async def _relaunch() -> None:
        nonlocal relaunches
        relaunches += 1
        if relaunches >= 2:
            backend.title = "Alfred Hitchcock Presents"

    channel = Channel(
        number=77,
        name="MeTV",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/77",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=10.0,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=0.3,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.att.tv",
        options,
        launch_at + 10.0,
        prior_app=None,
        launch_at=launch_at,
        relaunch=_relaunch,
        leftover_title="Red Tomahawk",
    )
    assert ready is True
    assert relaunches == 2


@pytest.mark.asyncio
async def test_wait_ready_relaunches_once_then_plays(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.IDLE
    relaunches = 0

    async def _relaunch() -> None:
        nonlocal relaunches
        relaunches += 1
        backend.playback = PlaybackState.PLAYING
        backend.title = "MLB"

    channel = Channel(
        number=213,
        name="MLB",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/213",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=10.0,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=0.4,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.att.tv",
        options,
        launch_at + 10.0,
        prior_app=None,
        launch_at=launch_at,
        relaunch=_relaunch,
    )
    assert ready is True
    assert relaunches == 1
    assert time.monotonic() - launch_at >= 0.4


@pytest.mark.asyncio
async def test_wait_ready_playing_before_relaunch_skips_retry(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.PLAYING
    backend.title = "MLB"
    relaunches = 0

    async def _relaunch() -> None:
        nonlocal relaunches
        relaunches += 1

    channel = Channel(
        number=213,
        name="MLB",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/213",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=5.0,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=2.0,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.att.tv",
        options,
        launch_at + 5.0,
        prior_app=None,
        launch_at=launch_at,
        relaunch=_relaunch,
    )
    assert ready is True
    assert relaunches == 0
    assert time.monotonic() - launch_at < 1.5


@pytest.mark.asyncio
async def test_wait_ready_relaunch_zero_keeps_idle_fallback(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.IDLE
    relaunches = 0

    async def _relaunch() -> None:
        nonlocal relaunches
        relaunches += 1

    channel = Channel(
        number=213,
        name="MLB",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/213",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=10.0,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=0,
    )
    launch_at = time.monotonic()
    # Callback present but seconds=0 → can_relaunch false; old IDLE fallback.
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.att.tv",
        options,
        launch_at + 10.0,
        prior_app=None,
        launch_at=launch_at,
        relaunch=_relaunch,
    )
    assert ready is True
    assert relaunches == 0
    assert time.monotonic() - launch_at >= 3.0


@pytest.mark.asyncio
async def test_wait_ready_after_relaunch_never_playing_fails(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.IDLE
    relaunches = 0

    async def _relaunch() -> None:
        nonlocal relaunches
        relaunches += 1

    channel = Channel(
        number=213,
        name="MLB",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/213",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=1.2,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=0.3,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.att.tv",
        options,
        launch_at + options.tune_timeout_seconds,
        prior_app=None,
        launch_at=launch_at,
        relaunch=_relaunch,
    )
    assert ready is False
    assert relaunches == 2


@pytest.mark.asyncio
async def test_failed_relaunch_does_not_extend_splash_wait(tmp_path, monkeypatch):
    """A launch that throws after HOME must not add the 45s splash grace."""
    from apituner import tuner_manager as tm

    monkeypatch.setattr(tm, "_STALE_SPLASH_GRACE_SECONDS", 1.5)
    monkeypatch.setattr(tm, "_STALE_SPLASH_GRACE_MIN_TIMEOUT", 0.0)

    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.IDLE
    relaunches = 0

    async def _relaunch() -> None:
        nonlocal relaunches
        relaunches += 1
        raise RuntimeError("HTTP verb {}POST unhandled")

    channel = Channel(
        number=77,
        name="MeTV",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/77",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=0.9,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=0.2,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.att.tv",
        options,
        launch_at + options.tune_timeout_seconds,
        prior_app=None,
        launch_at=launch_at,
        relaunch=_relaunch,
    )
    elapsed = time.monotonic() - launch_at
    assert ready is False
    assert relaunches == 2
    assert elapsed < 1.6


@pytest.mark.asyncio
async def test_deeplink_tune_relaunches_via_backend_launch(tmp_path, monkeypatch):
    """Full deeplink path: second launch after IDLE, then PLAYING."""
    from apituner import tuner_manager as tm
    from apituner.models import ControlConfig, Tuner

    monkeypatch.setattr(tm, "_HOME_BEFORE_DEEPLINK_SECONDS", 0.0)

    class CountingBackend(StubBackend):
        def __init__(self) -> None:
            super().__init__()
            self.launches: list[tuple] = []
            self.homes = 0
            self.current = "com.android.launcher"

        async def stop(self) -> None:
            self.homes += 1
            self.current = "com.android.launcher"

        async def launch(
            self,
            *,
            package,
            deeplink=None,
            component=None,
            action=None,
            extras=None,
            clear_task=False,
        ):
            self.launches.append((package, deeplink, clear_task))
            self.current = package
            # First launch stays IDLE; second starts playback.
            if len(self.launches) >= 2:
                self.playback = PlaybackState.PLAYING
                self.title = "MLB"
            else:
                self.playback = PlaybackState.IDLE

        async def get_info(self):
            from apituner.backends.base import DeviceInfo

            return DeviceInfo(packages=["com.att.tv"])

    store = ConfigStore(data_dir=tmp_path)
    store.config.options = GlobalOptions(
        wait_for_playback=True,
        stream_during_tune=False,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=0.4,
        tune_timeout_seconds=8.0,
    )
    store.config.tuners = [
        Tuner(
            id="t1",
            name="Stream",
            control=ControlConfig(type="http_agent", host="192.0.2.1"),
            stream_endpoint="http://192.0.2.2/s",
        )
    ]
    store.config.channels = [
        Channel(
            number=213,
            name="MLB",
            package_name="com.att.tv",
            url="https://stream.directv.com/watch/213",
        )
    ]
    store.save()

    manager = TunerManager(store)
    backend = CountingBackend()
    manager._backends["t1"] = backend

    lease = await manager.lease(store.config.channels[0])
    assert len(backend.launches) == 2
    assert backend.launches[0][1] == "https://stream.directv.com/watch/213"
    assert backend.launches[1][1] == "https://stream.directv.com/watch/213"
    assert backend.launches[0][2] is True
    assert backend.launches[1][2] is True
    assert backend.homes >= 1
    await manager.release(lease)


@pytest.mark.asyncio
async def test_deeplink_tune_relaunch_timeout_raises(tmp_path, monkeypatch):
    from apituner import tuner_manager as tm
    from apituner.models import ControlConfig, Tuner
    from apituner.tuner_manager import TuneFailed

    monkeypatch.setattr(tm, "_HOME_BEFORE_DEEPLINK_SECONDS", 0.0)

    class IdleBackend(StubBackend):
        def __init__(self) -> None:
            super().__init__()
            self.launches = 0
            self.current = "com.android.launcher"
            self.playback = PlaybackState.IDLE

        async def launch(self, *, package, deeplink=None, component=None, action=None, extras=None):
            self.launches += 1
            self.current = package

        async def get_info(self):
            from apituner.backends.base import DeviceInfo

            return DeviceInfo(packages=["com.att.tv"])

    store = ConfigStore(data_dir=tmp_path)
    store.config.options = GlobalOptions(
        wait_for_playback=True,
        stream_during_tune=False,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=0.3,
        tune_timeout_seconds=1.2,
        retry_on_other_tuner=False,
    )
    store.config.tuners = [
        Tuner(
            id="t1",
            name="Stream",
            control=ControlConfig(type="http_agent", host="192.0.2.1"),
            stream_endpoint="http://192.0.2.2/s",
        )
    ]
    store.config.channels = [
        Channel(
            number=213,
            name="MLB",
            package_name="com.att.tv",
            url="https://stream.directv.com/watch/213",
        )
    ]
    store.save()

    manager = TunerManager(store)
    backend = IdleBackend()
    manager._backends["t1"] = backend

    with pytest.raises(TuneFailed):
        await manager.lease(store.config.channels[0])
    assert backend.launches == 3


@pytest.mark.asyncio
async def test_deeplink_tune_homes_stuck_splash_before_first_launch(tmp_path, monkeypatch):
    """DirecTV already open on splash: HOME, then CLEAR_TASK deeplink."""
    from apituner import tuner_manager as tm
    from apituner.models import ControlConfig, Tuner

    monkeypatch.setattr(tm, "_HOME_BEFORE_DEEPLINK_SECONDS", 0.0)

    class SplashBackend(StubBackend):
        def __init__(self) -> None:
            super().__init__()
            self.homes = 0
            self.launches: list[bool] = []
            self.current = "com.att.tv"
            self.playback = PlaybackState.IDLE

        async def stop(self) -> None:
            self.homes += 1
            self.current = "com.android.launcher"

        async def launch(
            self,
            *,
            package,
            deeplink=None,
            component=None,
            action=None,
            extras=None,
            clear_task=False,
        ):
            self.launches.append(clear_task)
            self.current = package
            self.playback = PlaybackState.PLAYING
            self.title = "Cozi TV"

        async def get_info(self):
            from apituner.backends.base import DeviceInfo

            return DeviceInfo(packages=["com.att.tv"])

    store = ConfigStore(data_dir=tmp_path)
    store.config.options = GlobalOptions(
        wait_for_playback=True,
        stream_during_tune=False,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=6.0,
        tune_timeout_seconds=5.0,
    )
    store.config.tuners = [
        Tuner(
            id="t1",
            name="Stream",
            control=ControlConfig(type="http_agent", host="192.0.2.1"),
            stream_endpoint="http://192.0.2.2/s",
        )
    ]
    store.config.channels = [
        Channel(
            number=80,
            name="Cozi TV",
            package_name="com.att.tv",
            url="https://stream.directv.com/watch/80",
        )
    ]
    store.save()

    manager = TunerManager(store)
    backend = SplashBackend()
    manager._backends["t1"] = backend

    lease = await manager.lease(store.config.channels[0])
    assert backend.homes == 1
    assert backend.launches == [True]
    await manager.release(lease)


@pytest.mark.asyncio
async def test_deeplink_tune_playing_same_app_does_not_home_first(tmp_path, monkeypatch):
    """Warm DirecTV already PLAYING should not HOME before the first VIEW."""
    from apituner import tuner_manager as tm
    from apituner.models import ControlConfig, Tuner

    monkeypatch.setattr(tm, "_HOME_BEFORE_DEEPLINK_SECONDS", 0.0)

    class WarmBackend(StubBackend):
        def __init__(self) -> None:
            super().__init__()
            self.homes = 0
            self.launches = 0
            self.current = "com.att.tv"
            self.playback = PlaybackState.PLAYING
            self.title = "Chicago Fire"

        async def stop(self) -> None:
            self.homes += 1

        async def launch(
            self,
            *,
            package,
            deeplink=None,
            component=None,
            action=None,
            extras=None,
            clear_task=False,
        ):
            self.launches += 1
            self.current = package
            self.title = "The Golden Girls"

        async def get_info(self):
            from apituner.backends.base import DeviceInfo

            return DeviceInfo(packages=["com.att.tv"])

    store = ConfigStore(data_dir=tmp_path)
    store.config.options = GlobalOptions(
        wait_for_playback=True,
        stream_during_tune=False,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=6.0,
        tune_timeout_seconds=5.0,
    )
    store.config.tuners = [
        Tuner(
            id="t1",
            name="Stream",
            control=ControlConfig(type="http_agent", host="192.0.2.1"),
            stream_endpoint="http://192.0.2.2/s",
        )
    ]
    store.config.channels = [
        Channel(
            number=77,
            name="MeTV",
            package_name="com.att.tv",
            url="https://stream.directv.com/watch/77",
        )
    ]
    store.save()

    manager = TunerManager(store)
    backend = WarmBackend()
    manager._backends["t1"] = backend

    lease = await manager.lease(store.config.channels[0])
    assert backend.homes == 0
    assert backend.launches == 1
    await manager.release(lease)


@pytest.mark.asyncio
async def test_wait_ready_extends_idle_splash_then_plays(tmp_path, monkeypatch):
    from apituner import tuner_manager as tm

    monkeypatch.setattr(tm, "_STALE_SPLASH_GRACE_SECONDS", 0.8)
    monkeypatch.setattr(tm, "_STALE_SPLASH_GRACE_MIN_TIMEOUT", 0.4)

    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.IDLE
    relaunches = 0

    async def _relaunch() -> None:
        nonlocal relaunches
        relaunches += 1

    async def _snap():
        if relaunches >= 2 and time.monotonic() - launch_at > 0.6:
            backend.playback = PlaybackState.PLAYING
            backend.title = "Funny You Should Ask"
        return backend.playback, backend.current, getattr(backend, "title", None)

    backend.playback_snapshot = _snap  # type: ignore[method-assign]

    channel = Channel(
        number=80,
        name="Cozi TV",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/80",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=0.5,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=0.12,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.att.tv",
        options,
        launch_at + options.tune_timeout_seconds,
        prior_app=None,
        launch_at=launch_at,
        relaunch=_relaunch,
    )
    assert ready is True
    assert relaunches == 2
    assert time.monotonic() - launch_at >= 0.5


@pytest.mark.asyncio
async def test_wait_ready_does_not_extend_leftover_playing(tmp_path, monkeypatch):
    from apituner import tuner_manager as tm

    monkeypatch.setattr(tm, "_STALE_SPLASH_GRACE_SECONDS", 2.0)
    monkeypatch.setattr(tm, "_STALE_SPLASH_GRACE_MIN_TIMEOUT", 0.3)

    store = ConfigStore(data_dir=tmp_path)
    manager = TunerManager(store)
    backend = StubBackend()
    backend.current = "com.att.tv"
    backend.playback = PlaybackState.PLAYING
    backend.title = "YES Network"

    channel = Channel(
        number=4,
        name="NBC-WNBC",
        package_name="com.att.tv",
        url="https://stream.directv.com/watch/4",
    )
    options = GlobalOptions(
        wait_for_playback=True,
        tune_timeout_seconds=0.45,
        ready_settle_seconds=0.0,
        deeplink_relaunch_seconds=0.15,
    )
    launch_at = time.monotonic()
    ready = await manager._wait_ready(
        backend,
        channel,
        "com.att.tv",
        options,
        launch_at + options.tune_timeout_seconds,
        prior_app="com.att.tv",
        launch_at=launch_at,
        leftover_title="YES Network",
        relaunch=lambda: asyncio.sleep(0),
    )
    assert ready is False
    assert time.monotonic() - launch_at < 1.2
