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


def test_fruitdeeplinks_base_url_normalizes_missing_scheme():
    from apituner.fruitdeeplinks import fruitdeeplinks_base_url
    from apituner.models import GlobalOptions

    opts = GlobalOptions(fruitdeeplinks_url="192.168.1.17:6655")
    assert fruitdeeplinks_base_url(opts) == "http://192.168.1.17:6655"

    opts2 = GlobalOptions(fruitdeeplinks_url="https://fdl.lan:6655/")
    assert fruitdeeplinks_base_url(opts2) == "https://fdl.lan:6655"


def test_deeplink_catalog_includes_apple_f1():
    from apituner.deeplink_catalog import packages_for

    mapped = packages_for("apple_f1", profile="google_tv")
    assert mapped is not None
    assert mapped[0] == "com.apple.atve.androidtv.appletv"
    assert mapped[1] == "com.apple.atve.amazon.appletv"


def test_channels_from_adb_lanes_deterministic_ids():
    providers = [
        {"provider_code": "apple_mls", "adb_enabled": 1, "adb_lane_count": 2},
        {"provider_code": "sportscenter", "adb_enabled": 1, "adb_lane_count": 1},
    ]
    channels1, _ = channels_from_adb_lanes(
        providers,
        base_url="http://192.0.2.40:6655",
        profile="google_tv",
        start_number=3000,
    )
    channels2, _ = channels_from_adb_lanes(
        providers,
        base_url="http://192.0.2.40:6655",
        profile="google_tv",
        start_number=4000,
    )
    assert [c.id for c in channels1] == [c.id for c in channels2]
    assert len({c.id for c in channels1}) == 3


def test_apply_fdl_channels_preserves_customizations(tmp_path):
    from apituner.config import ConfigStore
    from apituner.fruitdeeplinks import apply_fdl_channels
    from apituner.models import Channel

    store = ConfigStore(data_dir=tmp_path)
    ch_existing = Channel(
        number=3016,
        name="MLS 1 (Colorado Rapids)",
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
            number=9000,
            name="apple_mls 1",
            provider_name="apple_mls",
            package_name="com.apple.atve.androidtv.appletv",
            url="http://192.0.2.99:6655/api/adb/lanes/apple_mls/1/deeplink",
            source="fruitdeeplinks",
            key_macro=["DPAD_CENTER"],
        )
    ]
    apply_fdl_channels(store, incoming)

    updated = store.config.channels[0]
    # Existing custom name, number, macro, and stable channel ID were preserved
    assert updated.id == ch_existing.id
    assert updated.number == ch_existing.number
    assert updated.name == "MLS 1 (Colorado Rapids)"
    assert updated.url == "http://192.0.2.99:6655/api/adb/lanes/apple_mls/1/deeplink"
    assert updated.key_macro == ["DPAD_CENTER", "DPAD_DOWN", "DPAD_CENTER"]
    assert updated.configuration_uuid == "custom-uuid"
    assert updated.tvc_guide_stationid == "99999"


def test_duplicate_names_do_not_reuse_a_legacy_id(tmp_path):
    from apituner.config import ConfigStore
    from apituner.fruitdeeplinks import apply_fdl_channels
    from apituner.models import Channel

    store = ConfigStore(data_dir=tmp_path)
    old = Channel(
        number=9000, name="Game", package_name="com.example", source="fruitdeeplinks"
    )
    store.import_channels([old.model_dump()], replace=True)
    incoming = [
        old.model_copy(update={"id": f"lane-{lane}", "number": str(9000 + lane)})
        for lane in (1, 2)
    ]

    assert apply_fdl_channels(store, incoming) == 2
    assert [channel.id for channel in store.config.channels] == ["lane-1", "lane-2"]


async def test_m3u_fallback_and_guide_preserve_lineup_on_resync(tmp_path, monkeypatch):
    import httpx

    from apituner.config import ConfigStore
    from apituner.fruitdeeplinks import sync_fruitdeeplinks
    from apituner.playlist import build_m3u
    from apituner.hdhr.xmltv import fetch_fdl_xmltv
    from apituner.models import Channel

    store = ConfigStore(data_dir=tmp_path)
    store.config.options.fruitdeeplinks_url = "fdl.test:6655"
    old = Channel(
        number=9000,
        name="Custom sports",
        package_name="com.espn.score_center",
        url="http://old.test:6655/api/adb/lanes/sportscenter/1/deeplink",
        source="fruitdeeplinks",
        key_macro=["DPAD_CENTER"],
    )
    manual = Channel(number=9001, name="Manual", package_name="com.example")
    store.import_channels([old.model_dump(), manual.model_dump()], replace=True)
    guide = '<tv><channel id="sportscenter01"/></tv>'
    playlist = (
        '#EXTM3U\n#EXTINF:-1 tvg-chno="1",New lane\n'
        'http://fdl.test:6655/api/adb/lanes/aiv/1/deeplink\n'
        '#EXTINF:-1 tvg-chno="1",Renamed sports\n'
        'http://fdl.test:6655/api/adb/lanes/sportscenter/1/deeplink\n'
    )

    def respond(request):
        assert request.url.scheme == "http"
        assert request.url.host == "fdl.test"
        if request.url.path == "/m3u/adb":
            return httpx.Response(200, text=playlist)
        if request.url.path == "/out/adb_lanes.xml":
            return httpx.Response(200, text=guide)
        return httpx.Response(404)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    prior_lineup = build_m3u(store.config.channels, "http://apituner.test")
    assert f'channel-id="{old.id}"' in prior_lineup
    for _ in range(2):
        assert (await sync_fruitdeeplinks(store))["mode"] == "m3u_adb"
        assert await fetch_fdl_xmltv(store.config.options) == guide
        channels = {channel.name: channel for channel in store.config.channels}
        assert channels["Manual"].number == "9001"
        assert channels["New lane"].number == "9002"
        assert channels["Custom sports"].number == "9000"
        assert channels["Custom sports"].id == old.id
        assert channels["Custom sports"].key_macro == ["DPAD_CENTER"]
        assert f'channel-id="{old.id}"' in build_m3u(
            store.config.channels, "http://apituner.test"
        )
