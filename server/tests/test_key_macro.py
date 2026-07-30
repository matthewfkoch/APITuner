"""Tests for key_macro normalization."""

from __future__ import annotations

from apituner.keys import key_requires_dpad, normalize_key_macro
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
    assert key_requires_dpad("DPAD_CENTER")
    assert key_requires_dpad("MEDIA_STOP")
