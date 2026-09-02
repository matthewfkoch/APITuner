from apituner.channels import (
    ChannelValidationError,
    find_duplicate_ids,
    resolve_channel,
    validate_unique_ids,
)
from apituner.config import ConfigStore
from apituner.models import Channel


def _channel(number: int, name: str = "Test", *, cid: str | None = None) -> Channel:
    kwargs: dict = {"number": number, "name": name, "package_name": "com.example.app"}
    if cid is not None:
        kwargs["id"] = cid
    return Channel(**kwargs)


def test_find_duplicate_ids():
    a = _channel(1, cid="a" * 32)
    b = _channel(2, cid="b" * 32)
    c = _channel(3, cid="a" * 32)
    assert find_duplicate_ids([a, b, c]) == ["a" * 32]


def test_validate_unique_ids_raises():
    try:
        validate_unique_ids(
            [_channel(5, "ESPN", cid="x" * 32), _channel(5, "NBC", cid="x" * 32)]
        )
        assert False, "expected ChannelValidationError"
    except ChannelValidationError as exc:
        assert "x" * 32 in str(exc)


def test_import_accepts_duplicate_numbers(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    count = store.import_channels(
        [
            {"number": 213, "name": "MLB Network", "package_name": "com.a"},
            {"number": 213, "name": "MLB Network Alternate", "package_name": "com.b"},
        ],
        replace=True,
    )
    assert count == 2
    assert len(store.config.channels) == 2
    assert store.config.channels[0].number == "213"
    assert store.config.channels[1].number == "213"
    assert store.config.channels[0].id != store.config.channels[1].id


def test_import_merge_by_id(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    store.import_channels(
        [{"id": "a" * 32, "number": 1, "name": "Old", "package_name": "com.old"}],
        replace=True,
    )
    store.import_channels(
        [{"id": "a" * 32, "number": 1, "name": "New", "package_name": "com.new"}],
        replace=False,
    )
    assert len(store.config.channels) == 1
    assert store.config.channels[0].name == "New"


def test_import_merge_appends_without_id(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    store.import_channels(
        [{"number": 1, "name": "Old", "package_name": "com.old"}],
        replace=True,
    )
    store.import_channels(
        [{"number": 1, "name": "New", "package_name": "com.new"}],
        replace=False,
    )
    assert len(store.config.channels) == 2
    names = {c.name for c in store.config.channels}
    assert names == {"Old", "New"}


def test_export_includes_action_and_key_macro(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    store.config.channels = [
        Channel(
            number=9,
            name="ESPN",
            package_name="com.yttv",
            url="https://example.com",
            action="android.intent.action.VIEW",
            key_macro=["DPAD_CENTER"],
        )
    ]
    exported = store.export_channels()
    assert exported[0]["action"] == "android.intent.action.VIEW"
    assert exported[0]["key_macro"] == ["DPAD_CENTER"]
    assert exported[0]["number"] == 9
    assert "id" not in exported[0]


def test_export_native_includes_id(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    ch = _channel(1, cid="c" * 32)
    store.config.channels = [ch]
    exported = store.export_channels(native=True)
    assert exported[0]["id"] == "c" * 32


def test_import_normalizes_adbtuner_quirks(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    count = store.import_channels(
        [
            {
                "number": None,
                "name": "WSVN 7",
                "package_name": "com.google.android.youtube.tvunplugged",
                "alternate_package_name": "",
                "tvc_guide_stationid": 21220,
                "sort_order": "3.0",
            }
        ],
        replace=True,
    )
    assert count == 1
    ch = store.config.channels[0]
    assert ch.number == "3"
    assert ch.tvc_guide_stationid == "21220"
    assert ch.alternate_package_name is None
    assert len(ch.id) == 32


def test_import_coerces_string_channel_number(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    store.import_channels(
        [{"number": "245.0", "name": "ESPN 4K", "package_name": "com.a"}],
        replace=True,
    )
    assert store.config.channels[0].number == "245"


def test_import_null_number_without_sort_order_is_clear(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    try:
        store.import_channels(
            [
                {
                    "number": None,
                    "name": "WSVN 7",
                    "package_name": "com.google.android.youtube.tvunplugged",
                }
            ],
            replace=True,
        )
        assert False, "expected ChannelValidationError"
    except ChannelValidationError as exc:
        msg = str(exc)
        assert "WSVN 7" in msg
        assert "missing channel number" in msg


def test_config_load_migrates_missing_ids(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        """{
  "tuners": [],
  "channels": [
    {"number": 1, "name": "ABC", "package_name": "com.example.app", "url": ""}
  ],
  "options": {}
}"""
    )
    store = ConfigStore(data_dir=tmp_path)
    assert len(store.config.channels[0].id) == 32


def test_resolve_channel_by_id_and_number():
    ch1 = _channel(213, "MLB Network", cid="a" * 32)
    ch2 = _channel(213, "MLB Alternate", cid="b" * 32)
    channels = [ch1, ch2]
    got, err = resolve_channel(channels, "a" * 32)
    assert err is None
    assert got is ch1
    got, err = resolve_channel(channels, "213")
    assert got is None
    assert err == "ambiguous"


def test_resolve_channel_unique_number():
    ch = _channel(5, "NBC")
    got, err = resolve_channel([ch], "5")
    assert err is None
    assert got is ch
    got, err = resolve_channel([ch], "5.1")
    assert err is None
    assert got is ch
