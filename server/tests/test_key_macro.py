"""Tests for key_macro normalization."""

from __future__ import annotations

from apituner.keys import key_requires_dpad, normalize_key_macro, parse_key_macro_sleep
from apituner.models import Channel


def test_key_macro_split_semicolon_and_comma():
    assert normalize_key_macro("DPAD_CENTER;DPAD_CENTER") == [
        "DPAD_CENTER",
        "DPAD_CENTER",
    ]
    assert normalize_key_macro(["DPAD_CENTER;DPAD_DOWN", "HOME"]) == [
        "DPAD_CENTER",
        "DPAD_DOWN",
        "HOME",
    ]
    assert normalize_key_macro("KEYCODE_DPAD_CENTER, KEYCODE_DPAD_CENTER") == [
        "DPAD_CENTER",
        "DPAD_CENTER",
    ]


def test_channel_model_normalizes_key_macro():
    ch = Channel(
        number=3101,
        name="HBO",
        package_name="com.wbd.stream",
        url="https://play.hbomax.com/x",
        key_macro=["DPAD_CENTER;DPAD_CENTER"],
    )
    assert ch.key_macro == ["DPAD_CENTER", "DPAD_CENTER"]


def test_key_requires_dpad():
    assert not key_requires_dpad("BACK")
    assert not key_requires_dpad("HOME")
    assert not key_requires_dpad("SLEEP 4")
    assert not key_requires_dpad("sleep 0.5")
    assert key_requires_dpad("DPAD_CENTER")
    assert key_requires_dpad("MEDIA_STOP")


def test_key_macro_sleep_tokens():
    assert parse_key_macro_sleep("sleep 4") == 4.0
    assert parse_key_macro_sleep("SLEEP 0.5") == 0.5
    assert parse_key_macro_sleep("SLEEP_2.5") == 2.5
    assert parse_key_macro_sleep("WAIT 3") == 3.0
    assert parse_key_macro_sleep("DPAD_CENTER") is None
    assert normalize_key_macro("sleep 4, KEYCODE_DPAD_CENTER, sleep 0.5, DPAD_CENTER") == [
        "SLEEP 4",
        "DPAD_CENTER",
        "SLEEP 0.5",
        "DPAD_CENTER",
    ]

