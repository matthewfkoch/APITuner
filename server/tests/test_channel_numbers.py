from apituner.channel_numbers import (
    channel_number_sort_key,
    export_channel_number,
    normalize_channel_number,
    occupied_integer_numbers,
)
from apituner.channels import resolve_channel
from apituner.config import ConfigStore
from apituner.models import Channel
from apituner.playlist import build_m3u


def test_normalize_whole_and_subchannel_numbers():
    assert normalize_channel_number(100) == "100"
    assert normalize_channel_number("100.1") == "100.1"
    assert normalize_channel_number("100.2") == "100.2"
    assert normalize_channel_number("245.0") == "245"
    assert normalize_channel_number("3.0") == "3"
    assert normalize_channel_number(100.1) == "100.1"


def test_channel_number_sort_key_orders_major_minor():
    channels = ["9", "100.2", "100.1", "10", "100"]
    assert sorted(channels, key=channel_number_sort_key) == [
        "9",
        "10",
        "100",
        "100.1",
        "100.2",
    ]


def test_import_dotted_subchannels(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    store.import_channels(
        [
            {"number": "100.1", "name": "Sub A", "package_name": "com.a"},
            {"number": "100.2", "name": "Sub B", "package_name": "com.b"},
        ],
        replace=True,
    )
    numbers = [ch.number for ch in store.config.channels]
    assert numbers == ["100.1", "100.2"]


def test_resolve_channel_dotted_numbers():
    ch1 = Channel(number="100.1", name="A", package_name="com.a")
    ch2 = Channel(number="100.2", name="B", package_name="com.b")
    channels = [ch1, ch2]
    got, err = resolve_channel(channels, "100.1")
    assert err is None
    assert got is ch1
    got, err = resolve_channel(channels, "100")
    assert got is None
    assert err == "not_found"


def test_export_channel_number_json_shape():
    assert export_channel_number("213") == 213
    assert export_channel_number("213.1") == 213.1


def test_occupied_integer_numbers_skips_subchannels():
    assert occupied_integer_numbers(["9000", "213.1", "213"]) == {9000, 213}


def test_build_m3u_dotted_guide_numbers():
    channels = [
        Channel(number="100.1", name="Sub A", package_name="com.a"),
        Channel(number="100.2", name="Sub B", package_name="com.b"),
    ]
    m3u = build_m3u(channels, "http://127.0.0.1:6592")
    assert 'tvg-chno="100.1"' in m3u
    assert 'tvg-chno="100.2"' in m3u
