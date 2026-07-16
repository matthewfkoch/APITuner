from apituner.channels import find_duplicate_numbers, validate_channel_numbers
from apituner.channels import ChannelValidationError
from apituner.config import ConfigStore
from apituner.models import Channel


def _channel(number: int, name: str = "Test") -> Channel:
    return Channel(number=number, name=name, package_name="com.example.app")


def test_find_duplicate_numbers():
    chans = [_channel(1), _channel(2), _channel(1), _channel(3), _channel(2)]
    assert find_duplicate_numbers(chans) == [1, 2]


def test_validate_channel_numbers_raises():
    try:
        validate_channel_numbers([_channel(5, "ESPN"), _channel(5, "NBC")])
        assert False, "expected ChannelValidationError"
    except ChannelValidationError as exc:
        msg = str(exc)
        assert "5" in msg
        assert "ESPN" in msg
        assert "NBC" in msg


def test_import_rejects_duplicates_in_batch(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    data = [
        {"number": 1, "name": "A", "package_name": "com.a"},
        {"number": 1, "name": "B", "package_name": "com.b"},
    ]
    try:
        store.import_channels(data, replace=True)
        assert False, "expected ChannelValidationError"
    except ChannelValidationError as exc:
        assert "1" in str(exc)
        assert "A" in str(exc)
        assert "B" in str(exc)


def test_import_merge_replaces_by_number(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    store.import_channels(
        [{"number": 1, "name": "Old", "package_name": "com.old"}],
        replace=True,
    )
    store.import_channels(
        [{"number": 1, "name": "New", "package_name": "com.new"}],
        replace=False,
    )
    assert len(store.config.channels) == 1
    assert store.config.channels[0].name == "New"


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
    assert ch.number == 3
    assert ch.tvc_guide_stationid == "21220"
    assert ch.alternate_package_name is None


def test_import_coerces_string_channel_number(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    store.import_channels(
        [{"number": "245.0", "name": "ESPN 4K", "package_name": "com.a"}],
        replace=True,
    )
    assert store.config.channels[0].number == 245


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
