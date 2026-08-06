"""Package coverage helpers for dashboard warnings."""

from __future__ import annotations

import pytest

from apituner.backends.base import Capabilities, DeviceInfo
from apituner.config import ConfigStore
from apituner.models import Channel, ControlConfig, Tuner
from apituner.package_coverage import build_package_coverage
from apituner.packages import package_candidates, package_installed, package_try_order
from apituner.tuner_manager import TunerManager


class FakeBackend:
    def __init__(self, packages: list[str]) -> None:
        self.capabilities = Capabilities(app_list=True)
        self._packages = packages

    async def list_apps(self):
        return [{"name": p, "packageName": p} for p in self._packages]

    async def get_info(self):
        return DeviceInfo(packages=list(self._packages))


def test_package_candidates_espn_family():
    assert package_candidates("com.espn.gtv") == [
        "com.espn.gtv",
        "com.espn.score_center",
    ]
    assert package_installed(["com.espn.score_center"], "com.espn.gtv")
    assert package_try_order(
        "com.espn.gtv",
        installed=["com.espn.score_center"],
    ) == ["com.espn.score_center", "com.espn.gtv"]
    assert package_try_order("com.espn.gtv", installed=None) == [
        "com.espn.gtv",
        "com.espn.score_center",
    ]


@pytest.mark.asyncio
async def test_build_package_coverage_missing(tmp_path, monkeypatch):
    store = ConfigStore(data_dir=tmp_path)
    store.config.tuners = [
        Tuner(
            id="t1",
            name="Fire",
            control=ControlConfig(type="http_agent", host="192.0.2.1"),
            stream_endpoint="http://192.0.2.2/s",
        )
    ]
    store.config.channels = [
        Channel(number=1507, name="ESPN", package_name="com.espn.gtv", url="14"),
        Channel(
            number=36,
            name="YTTV",
            package_name="com.google.android.youtube.tvunplugged",
            url="https://tv.youtube.com/x",
        ),
    ]
    store.save()
    manager = TunerManager(store)
    monkeypatch.setattr(
        manager,
        "get_backend",
        lambda tuner: FakeBackend(
            ["com.espn.score_center", "com.amazon.tv.launcher"]
        ),
    )

    report = await build_package_coverage(
        store.config.tuners, store.config.channels, manager
    )
    by_num = {c["number"]: c for c in report["channels"]}
    # gtv channel OK via family alternate score_center
    assert by_num[1507]["status"] == "ok"
    assert by_num[36]["status"] == "missing"
    assert report["summary"]["channels_missing"] == 1
