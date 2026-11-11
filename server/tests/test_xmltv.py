"""Tests for XMLTV remapping via Gracenote StationIDs."""

from __future__ import annotations

from apituner.hdhr.xmltv import build_xmltv
from apituner.models import Channel


def test_build_xmltv_remaps_by_station_id():
    ch_abc = Channel(
        id="abcabcabcabcabcabcabcabcabcabcab",
        number=200,
        name="ABC 7",
        package_name="com.yttv",
        tvc_guide_stationid="12007",
    )
    ch_noguide = Channel(
        id="defdefdefdefdefdefdefdefdefdefdefde",
        number=201,
        name="NoGuide",
        package_name="com.yttv",
    )
    channels = [ch_abc, ch_noguide]
    guide = [
        {
            "Channel": {"Station": "12007", "Number": "440", "Name": "ABC"},
            "Airings": [
                {
                    "Time": 1784000000,
                    "Duration": 1800,
                    "Title": "Local News",
                    "Categories": ["News"],
                    "ProgramID": "EP1",
                    "SeriesID": "SH1",
                    "OriginalDate": "2026-07-13",
                    "Image": "https://example.com/i.jpg",
                    "Raw": {
                        "stationId": "12007",
                        "program": {"longDescription": "Evening news"},
                    },
                }
            ],
        }
    ]
    xml = build_xmltv(channels, guide)
    assert f'channel id="{ch_abc.id}"' in xml
    assert "<display-name>ABC 7</display-name>" in xml
    assert f'channel="{ch_abc.id}"' in xml
    assert "<title>Local News</title>" in xml
    assert "<desc>Evening news</desc>" in xml
    assert f'channel id="{ch_noguide.id}"' in xml
    assert xml.count("<programme") == 1


FDL_XMLTV = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="ADB-sportscenter-001">
    <display-name>ADB SportsCenter 1</display-name>
  </channel>
  <programme start="20260812200000 +0000" stop="20260812230000 +0000" channel="ADB-sportscenter-001">
    <title>College Basketball</title>
  </programme>
  <programme start="20260812200000 +0000" stop="20260812230000 +0000" channel="OTHER">
    <title>Unmapped</title>
  </programme>
</tv>
"""


def test_rewrite_fdl_xmltv_maps_lane_dot_id():
    from apituner.hdhr.xmltv import rewrite_fdl_xmltv

    xml = """<?xml version="1.0"?><tv>
  <channel id="lane.1"><display-name>Fruit Lane 1</display-name></channel>
  <programme start="20260812200000 +0000" stop="20260812230000 +0000" channel="lane.1">
    <title>Soccer</title>
  </programme>
</tv>
"""
    ch_lane = Channel(
        id="laneidlaneidlaneidlaneidlaneidlanei",
        number=9000,
        name="Fruit Lane 1 (Apple TV)",
        package_name="com.apple.atve.androidtv.appletv",
        url="http://192.0.2.40:6655/whatson/1?include=deeplink",
        source="fruitdeeplinks",
    )
    programmes = rewrite_fdl_xmltv(xml, [ch_lane])
    assert f'channel="{ch_lane.id}"' in programmes
    assert "Soccer" in programmes
    from apituner.hdhr.xmltv import merge_fdl_programmes, rewrite_fdl_xmltv

    ch_espn = Channel(
        id="espnespnespnespnespnespnespnespne",
        number=9001,
        name="sportscenter 1",
        package_name="com.espn.score_center",
        url="http://192.0.2.40:6655/api/adb/lanes/sportscenter/1/deeplink?format=json",
        source="fruitdeeplinks",
    )
    programmes = rewrite_fdl_xmltv(FDL_XMLTV, [ch_espn])
    assert f'channel="{ch_espn.id}"' in programmes
    assert "College Basketball" in programmes
    assert "Unmapped" not in programmes
    base = build_xmltv([ch_espn], [])
    merged = merge_fdl_programmes(base, programmes)
    assert merged.count("<programme") == 1
    assert f'channel="{ch_espn.id}"' in merged


def test_rewrite_fdl_xmltv_maps_two_digit_unhyphenated_channel_ids():
    from apituner.hdhr.xmltv import rewrite_fdl_xmltv

    xml = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="sportscenter02">
    <display-name>ESPN+ V2</display-name>
  </channel>
  <channel id="aiv01">
    <display-name>Prime Video 01</display-name>
  </channel>
  <programme start="20261107183000 +0000" stop="20261107213500 +0000" channel="sportscenter02">
    <title>College Football: Lindenwood vs Gardner-Webb</title>
  </programme>
  <programme start="20261107190000 +0000" stop="20261107220000 +0000" channel="aiv01">
    <title>Prime Game</title>
  </programme>
</tv>
"""
    ch_sc2 = Channel(
        id="sc2id000000000000000000000000000",
        number=3022,
        name="sportscenter 2",
        package_name="com.espn.score_center",
        url="http://192.168.1.17:6655/api/adb/lanes/sportscenter/2/deeplink?dynamic_url_json_key=deeplink_url&format=json",
        source="fruitdeeplinks",
    )
    ch_aiv1 = Channel(
        id="aiv1id00000000000000000000000000",
        number=3010,
        name="aiv 1",
        package_name="com.amazon.amazonvideo.livingroom",
        url="http://192.168.1.17:6655/api/adb/lanes/aiv/1/deeplink?dynamic_url_json_key=deeplink_url&format=json",
        source="fruitdeeplinks",
    )
    programmes = rewrite_fdl_xmltv(xml, [ch_sc2, ch_aiv1])
    assert f'channel="{ch_sc2.id}"' in programmes
    assert "College Football: Lindenwood vs Gardner-Webb" in programmes
    assert f'channel="{ch_aiv1.id}"' in programmes
    assert "Prime Game" in programmes
