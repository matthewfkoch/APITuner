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
        validate_channel_numbers([_channel(5), _channel(5)])
        assert False, "expected ChannelValidationError"
    except ChannelValidationError as exc:
        assert "5" in str(exc)


def test_import_rejects_duplicates_in_batch(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    data = [
        {"number": 1, "name": "A", "package_name": "com.a"},
        {"number": 1, "name": "B", "package_name": "com.b"},
    ]
    try:
        store.import_channels(data, replace=True)
        assert False, "expected ChannelValidationError"
    except ChannelValidationError:
        pass


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
