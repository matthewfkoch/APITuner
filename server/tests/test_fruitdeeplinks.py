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
    assert [c.number for c in channels] == ["9001", "9002"]
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


def test_base_url_normalizes_missing_scheme():
    from apituner.fruitdeeplinks import _base_url
    from apituner.models import GlobalOptions

    opts = GlobalOptions(fruitdeeplinks_url="192.168.1.17:6655")
    assert _base_url(opts) == "http://192.168.1.17:6655"

    opts2 = GlobalOptions(fruitdeeplinks_url="https://fdl.lan:6655/")
    assert _base_url(opts2) == "https://fdl.lan:6655"


def test_deeplink_catalog_includes_apple_f1():
    from apituner.deeplink_catalog import packages_for

    mapped = packages_for("apple_f1", profile="google_tv")
    assert mapped is not None
    assert mapped[0] == "com.apple.atve.androidtv.appletv"
    assert mapped[1] == "com.apple.atve.amazon.appletv"


def test_channels_from_adb_lanes_defaults_apple_key_macro():
    providers = [
        {"provider_code": "apple_mls", "adb_enabled": 1, "adb_lane_count": 2},
        {"provider_code": "sportscenter", "adb_enabled": 1, "adb_lane_count": 1},
    ]
    channels, _ = channels_from_adb_lanes(
        providers,
        base_url="http://192.0.2.40:6655",
        profile="google_tv",
        start_number=3000,
    )
    apple_ch = [c for c in channels if c.provider_name == "apple_mls"]
    espn_ch = [c for c in channels if c.provider_name == "sportscenter"]
    assert all(c.key_macro == ["DPAD_CENTER"] for c in apple_ch)
    assert all(c.key_macro is None for c in espn_ch)


def test_apply_fdl_channels_preserves_customizations(tmp_path):
    from apituner.config import ConfigStore
    from apituner.fruitdeeplinks import apply_fdl_channels
    from apituner.models import Channel

    store = ConfigStore(data_dir=tmp_path)
    ch_existing = Channel(
        number=3016,
        name="apple_mls 1",
        provider_name="apple_mls",
        package_name="com.apple.atve.androidtv.appletv",
        url="http://192.0.2.40:6655/api/adb/lanes/apple_mls/1/deeplink",
        source="fruitdeeplinks",
        key_macro=["DPAD_CENTER", "DPAD_DOWN", "DPAD_CENTER"],
        configuration_uuid="custom-uuid",
        tvc_guide_stationid="99999",
    )
    store.import_channels([ch_existing.model_dump()], replace=True)

    incoming = [
        Channel(
            number=3016,
            name="apple_mls 1",
            provider_name="apple_mls",
            package_name="com.apple.atve.androidtv.appletv",
            url="http://192.0.2.40:6655/api/adb/lanes/apple_mls/1/deeplink",
            source="fruitdeeplinks",
            key_macro=["DPAD_CENTER"],
        )
    ]
    apply_fdl_channels(store, incoming)

    updated = store.config.channels[0]
    # Existing custom macro and stable channel ID were preserved
    assert updated.id == ch_existing.id
    assert updated.number == ch_existing.number
    assert updated.key_macro == ["DPAD_CENTER", "DPAD_DOWN", "DPAD_CENTER"]
    assert updated.configuration_uuid == "custom-uuid"
    assert updated.tvc_guide_stationid == "99999"


