"""Tests for XMLTV remapping via Gracenote StationIDs."""

from __future__ import annotations

from apituner.hdhr.xmltv import build_xmltv
from apituner.models import Channel


def test_build_xmltv_remaps_by_station_id():
    channels = [
        Channel(
            number=200,
            name="ABC 7",
            package_name="com.yttv",
            tvc_guide_stationid="12007",
        ),
        Channel(number=201, name="NoGuide", package_name="com.yttv"),
    ]
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
    assert 'channel id="200"' in xml
    assert "<display-name>ABC 7</display-name>" in xml
    assert 'channel="200"' in xml
    assert "<title>Local News</title>" in xml
    assert "<desc>Evening news</desc>" in xml
    assert 'channel id="201"' in xml
    # Channel 201 has no StationID / airings.
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
    channels = [
        Channel(
            number=9000,
            name="Fruit Lane 1 (Apple TV)",
            package_name="com.apple.atve.androidtv.appletv",
            url="http://192.0.2.40:6655/whatson/1?include=deeplink",
            source="fruitdeeplinks",
        )
    ]
    programmes = rewrite_fdl_xmltv(xml, channels)
    assert 'channel="9000"' in programmes
    assert "Soccer" in programmes
    from apituner.hdhr.xmltv import merge_fdl_programmes, rewrite_fdl_xmltv

    channels = [
        Channel(
            number=9001,
            name="sportscenter 1",
            package_name="com.espn.score_center",
            url="http://192.0.2.40:6655/api/adb/lanes/sportscenter/1/deeplink?format=json",
            source="fruitdeeplinks",
        )
    ]
    programmes = rewrite_fdl_xmltv(FDL_XMLTV, channels)
    assert 'channel="9001"' in programmes
    assert "College Basketball" in programmes
    assert "Unmapped" not in programmes
    base = build_xmltv(channels, [])
    merged = merge_fdl_programmes(base, programmes)
    assert merged.count("<programme") == 1
    assert 'channel="9001"' in merged
