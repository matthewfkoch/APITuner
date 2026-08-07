"""Tests for the network ADB App Play backend (mocked adb binary)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apituner.backends.adb import AdbBackend
from apituner.backends.base import Capabilities
from apituner.config_interpreter import run_commands
from apituner.models import ControlConfig, Tuner


class FakeAdbBackend(AdbBackend):
    """AdbBackend with in-memory shell recording (no real adb)."""

    def __init__(self) -> None:
        tuner = Tuner(
            id="t1",
            name="Fire",
            control=ControlConfig(type="adb", host="192.0.2.10", port=5555),
            stream_endpoint="http://192.0.2.11/s",
        )
        super().__init__(tuner)
        self.shell_cmds: list[str] = []
        self._connected = True

    async def connect(self) -> None:
        self._connected = True

    async def run_shell(self, command: str) -> str:
        self.shell_cmds.append(command)
        return "ok"


def test_adb_capabilities():
    assert AdbBackend.capabilities.dpad is True
    assert AdbBackend.capabilities.shell is True


@pytest.mark.asyncio
async def test_interpreter_force_stop_uses_real_am_on_adb():
    backend = FakeAdbBackend()
    await run_commands(
        backend,
        ["am force-stop '||TARGET_PACKAGE_NAME||'"],
        package="com.espn.score_center",
        identifier="0",
    )
    assert backend.shell_cmds == ["am force-stop com.espn.score_center"]


@pytest.mark.asyncio
async def test_interpreter_passes_input_keyevent_via_shell():
    backend = FakeAdbBackend()
    await run_commands(
        backend,
        ["input keyevent KEYCODE_DPAD_DOWN"],
        package="com.a",
        identifier="0",
    )
    assert backend.shell_cmds == ["input keyevent KEYCODE_DPAD_DOWN"]


@pytest.mark.asyncio
async def test_launch_open_app_tries_leanback_first():
    backend = FakeAdbBackend()
    await backend.launch(package="com.espn.score_center")
    assert backend.shell_cmds == [
        "monkey -p com.espn.score_center -c android.intent.category.LEANBACK_LAUNCHER 1"
    ]


@pytest.mark.asyncio
async def test_launch_open_app_falls_back_to_phone_launcher():
    class PhoneLauncherFake(FakeAdbBackend):
        async def run_shell(self, command: str) -> str:
            self.shell_cmds.append(command)
            if "LEANBACK_LAUNCHER" in command:
                return "** No activities found to run, monkey aborted."
            return "Events injected: 1"

    backend = PhoneLauncherFake()
    await backend.launch(package="com.example.phoneapp")
    assert backend.shell_cmds == [
        "monkey -p com.example.phoneapp -c android.intent.category.LEANBACK_LAUNCHER 1",
        "monkey -p com.example.phoneapp -c android.intent.category.LAUNCHER 1",
    ]


@pytest.mark.asyncio
async def test_launch_open_app_raises_when_both_categories_abort():
    from apituner.backends.base import BackendUnavailable

    class BothAbortFake(FakeAdbBackend):
        async def run_shell(self, command: str) -> str:
            self.shell_cmds.append(command)
            return "** No activities found to run, monkey aborted."

    backend = BothAbortFake()
    with pytest.raises(BackendUnavailable, match="no LAUNCHER or LEANBACK"):
        await backend.launch(package="com.missing.app")
    assert len(backend.shell_cmds) == 2



@pytest.mark.asyncio
async def test_launch_open_app_continues_after_nonzero_leanback():
    """Non-zero monkey exit on Leanback should still try phone LAUNCHER."""
    from apituner.backends.base import BackendUnavailable

    class NonZeroThenOk(FakeAdbBackend):
        async def run_shell(self, command: str) -> str:
            self.shell_cmds.append(command)
            if "LEANBACK_LAUNCHER" in command:
                raise BackendUnavailable("adb shell failed (255): monkey aborted")
            return "Events injected: 1"

    backend = NonZeroThenOk()
    await backend.launch(package="com.example.phoneapp")
    assert backend.shell_cmds == [
        "monkey -p com.example.phoneapp -c android.intent.category.LEANBACK_LAUNCHER 1",
        "monkey -p com.example.phoneapp -c android.intent.category.LAUNCHER 1",
    ]


@pytest.mark.asyncio
async def test_factory_builds_adb(tmp_path: Path):
    from apituner.backends.factory import build_backend

    tuner = Tuner(
        id="x",
        name="Fire",
        control=ControlConfig(type="adb", host="192.0.2.10"),
        stream_endpoint="http://192.0.2.11/s",
    )
    backend = build_backend(tuner, tmp_path)
    assert isinstance(backend, AdbBackend)
    assert backend.capabilities == Capabilities(
        keys=True,
        dpad=True,
        shell=True,
        current_app=True,
        playback_state=False,
        power=False,
        app_list=True,
        install=False,
    )
