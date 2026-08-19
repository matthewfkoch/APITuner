"""Tests for FruitDeepLinks / ADB M3U import."""

from __future__ import annotations

from apituner.config import ConfigStore
from apituner.m3u_import import channels_from_m3u, normalize_resolver_url
from apituner.models import Channel


SAMPLE_M3U = """#EXTM3U
#EXTINF:-1 channel-id="ADB-sportscenter-001" tvg-name="ADB SportsCenter 1" group-title="ADB SportsCenter",ADB SportsCenter 1
http://192.0.2.40:6655/api/adb/lanes/sportscenter/1/deeplink?format=text

#EXTINF:-1 channel-id="ADB-max-001" tvg-name="ADB Max 1" package-name="com.wbd.stream",ADB Max 1
http://192.0.2.40:6655/api/adb/lanes/max/1/deeplink
"""


def test_normalize_resolver_prefers_json_key():
    url = "http://192.0.2.40:6655/api/adb/lanes/max/1/deeplink?format=text"
    out = normalize_resolver_url(url)
    assert "format=json" in out
    assert "dynamic_url_json_key=deeplink_url" in out


def test_channels_from_m3u_fills_packages():
    channels, skipped = channels_from_m3u(
        SAMPLE_M3U, profile="google_tv", start_number=9000
    )
    assert skipped == []
    assert len(channels) == 2
    espn = channels[0]
    assert espn["number"] == 9000
    assert espn["name"] == "ADB SportsCenter 1"
    assert espn["package_name"] == "com.espn.score_center"
    assert espn["alternate_package_name"] == "com.espn.gtv"
    assert espn["provider_name"] == "sportscenter"
    assert espn["source"] == "fruitdeeplinks"
    assert "/api/adb/lanes/sportscenter/1/deeplink" in espn["url"]
    assert "dynamic_url_json_key=deeplink_url" in espn["url"]
    max_ch = channels[1]
    assert max_ch["package_name"] == "com.wbd.stream"
    assert max_ch["number"] == 9001


def test_rewrite_stream_m3u_to_whatson():
    text = """#EXTM3U
#EXTINF:-1 tvg-id="lane.1" tvg-chno="9000" group-title="FruitDeepLinks",Fruit Lane 1
http://192.0.2.40:6655/lane/1/stream.m3u8
"""
    channels, skipped = channels_from_m3u(text, profile="google_tv", start_number=1)
    assert skipped == []
    assert channels[0]["number"] == 9000
    assert "/whatson/1" in channels[0]["url"]
    assert "stream.m3u8" not in channels[0]["url"]
    assert channels[0]["package_name"]
    text = """#EXTM3U
#EXTINF:-1 tvg-name="Mystery",Mystery
http://192.0.2.40:6655/api/adb/lanes/notaprovider/1/deeplink?format=text
"""
    channels, skipped = channels_from_m3u(text, start_number=9000)
    assert channels == []
    assert len(skipped) == 1
    assert "notaprovider" in skipped[0]["reason"]


def test_import_source_replace_keeps_yttv(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    store.import_channels(
        [
            {
                "number": 1,
                "name": "YouTube TV",
                "package_name": "com.google.android.youtube.tvunplugged",
                "url": "https://tv.youtube.com/watch/x",
            }
        ],
        replace=True,
    )
    fdl, _ = channels_from_m3u(SAMPLE_M3U, profile="fire", start_number=9000)
    store.import_channels(fdl, replace=False)
    store.replace_source_channels(
        "fruitdeeplinks",
        [
            Channel(
                number=9000,
                name="sportscenter 1",
                package_name="com.espn.gtv",
                url=fdl[0]["url"],
                source="fruitdeeplinks",
            )
        ],
    )
    names = {ch.number: ch.name for ch in store.config.channels}
    assert names[1] == "YouTube TV"
    assert 9000 in names
    assert len([c for c in store.config.channels if c.source == "fruitdeeplinks"]) == 1
    exported = store.export_channels()
    assert any(row.get("source") == "fruitdeeplinks" for row in exported)
