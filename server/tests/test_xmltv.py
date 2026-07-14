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
