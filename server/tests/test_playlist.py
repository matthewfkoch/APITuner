from apituner.models import Channel
from apituner.playlist import build_m3u, filter_channels_by_provider


def test_build_m3u_sorted_and_stream_urls():
    channels = [
        Channel(number=36, name="ESPN", package_name="com.yttv", tvc_guide_stationid="32645"),
        Channel(number=1, name="ABC", package_name="com.yttv"),
    ]
    m3u = build_m3u(channels, "http://192.0.2.1:6592")
    lines = m3u.strip().splitlines()
    assert lines[0] == "#EXTM3U"
    assert "channel-id=\"1\"" in lines[1]
    assert "channel-number=\"1\"" in lines[1]
    assert "tvg-chno=\"1\"" in lines[1]
    assert lines[2] == "http://192.0.2.1:6592/stream/1"
    assert "tvc-guide-stationid=\"32645\"" in m3u
    assert "tvg-id=" not in m3u
    assert m3u.endswith("\n")
    # ESPN (36) should appear after ABC (1)
    assert m3u.index("ABC") < m3u.index("ESPN")


def test_build_m3u_escapes_quotes_in_name():
    channels = [Channel(number=2, name='Channel "HD"', package_name="com.x")]
    m3u = build_m3u(channels, "http://localhost:6592")
    assert "Channel 'HD'" in m3u or "tvg-name=\"Channel 'HD'\"" in m3u


def test_filter_channels_by_provider():
    channels = [
        Channel(number=1, name="ABC", package_name="com.yttv", provider_name="YouTube TV"),
        Channel(number=2, name="NFL", package_name="com.yttv", provider_name="Sunday Ticket"),
        Channel(number=3, name="CBS", package_name="com.yttv", provider_name="YouTube TV"),
    ]
    filtered = filter_channels_by_provider(channels, "YouTube TV")
    assert [c.number for c in filtered] == [1, 3]
    assert len(filter_channels_by_provider(channels, None)) == 3
