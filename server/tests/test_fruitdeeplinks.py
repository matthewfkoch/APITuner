"""Tests for FruitDeepLinks ADB lane sync helpers."""

from __future__ import annotations

from apituner.fruitdeeplinks import channels_from_adb_lanes, lane_resolver_url


def test_channels_from_adb_lanes_skips_disabled_and_unknown():
    providers = [
        {"provider_code": "sportscenter", "adb_enabled": 1, "adb_lane_count": 2},
        {"provider_code": "aiv", "adb_enabled": 0, "adb_lane_count": 5},
        {"provider_code": "mystery", "adb_enabled": 1, "adb_lane_count": 3},
    ]
    channels, skipped = channels_from_adb_lanes(
        providers,
        base_url="http://192.0.2.40:6655",
        profile="google_tv",
        start_number=9000,
        occupied={9000},
    )
    assert [c.number for c in channels] == [9001, 9002]
    assert all(c.source == "fruitdeeplinks" for c in channels)
    assert all(c.package_name == "com.espn.score_center" for c in channels)
    assert channels[0].alternate_package_name == "com.espn.gtv"
    assert "sportscenter/1/deeplink" in channels[0].url
    assert any("mystery" in row["reason"] for row in skipped)
    assert all(c.provider_name != "aiv" for c in channels)


def test_channels_from_virtual_lanes_whatson():
    from apituner.fruitdeeplinks import channels_from_virtual_lanes

    lanes = [
        {
            "lane_id": 1,
            "current": {"channel_name": "ESPN", "title": "Game"},
        },
        {"lane_id": 2, "current": None},
    ]
    channels, skipped = channels_from_virtual_lanes(
        lanes,
        base_url="http://192.0.2.40:6655",
        profile="google_tv",
        start_number=9000,
    )
    assert skipped == []
    assert channels[0].package_name == "com.espn.score_center"
    assert "/whatson/1" in channels[0].url
    assert "dynamic_url_json_key=deeplink_url" in channels[0].url
    assert channels[1].package_name == "com.apple.atve.androidtv.appletv"
    assert "/whatson/2" in channels[1].url
    url = lane_resolver_url("http://192.0.2.40:6655", "max", 1)
    assert url.startswith("http://192.0.2.40:6655/api/adb/lanes/max/1/deeplink")
    assert "format=json" in url
    assert "dynamic_url_json_key=deeplink_url" in url
