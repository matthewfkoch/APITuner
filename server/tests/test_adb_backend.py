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
async def test_launch_open_app_uses_monkey(tmp_path: Path):
    backend = FakeAdbBackend()
    await backend.launch(package="com.espn.score_center")
    assert any("monkey -p com.espn.score_center" in c for c in backend.shell_cmds)


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
