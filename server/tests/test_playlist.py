from apituner.models import Channel
from apituner.playlist import build_m3u, filter_channels_by_provider

ID_ABC = "a" * 32
ID_ESPN = "b" * 32


def test_build_m3u_sorted_and_stream_urls():
    channels = [
        Channel(
            id=ID_ESPN,
            number=36,
            name="ESPN",
            package_name="com.yttv",
            tvc_guide_stationid="32645",
        ),
        Channel(id=ID_ABC, number=1, name="ABC", package_name="com.yttv"),
    ]
    m3u = build_m3u(channels, "http://192.0.2.1:6592")
    lines = m3u.strip().splitlines()
    assert lines[0] == "#EXTM3U"
    assert f'channel-id="{ID_ABC}"' in lines[1]
    assert 'channel-number="1"' in lines[1]
    assert 'tvg-chno="1"' in lines[1]
    assert lines[2] == f"http://192.0.2.1:6592/stream/{ID_ABC}"
    assert "tvc-guide-stationid=\"32645\"" in m3u
    assert "tvg-id=" not in m3u
    assert m3u.endswith("\n")
    assert m3u.index("ABC") < m3u.index("ESPN")


def test_build_m3u_duplicate_guide_numbers():
    id_a = "c" * 32
    id_b = "d" * 32
    channels = [
        Channel(id=id_a, number=213, name="MLB Network", package_name="com.a"),
        Channel(id=id_b, number=213, name="MLB Network Alternate", package_name="com.b"),
    ]
    m3u = build_m3u(channels, "http://192.0.2.1:6592")
    assert m3u.count('tvg-chno="213"') == 2
    assert f"/stream/{id_a}" in m3u
    assert f"/stream/{id_b}" in m3u
    assert f'channel-id="{id_a}"' in m3u
    assert f'channel-id="{id_b}"' in m3u


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
    assert [c.number for c in filtered] == ["1", "3"]
    assert len(filter_channels_by_provider(channels, None)) == 3
