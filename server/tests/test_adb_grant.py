"""Unit tests for one-time Fire TV ADB permission grant helpers."""

from __future__ import annotations

import pytest

from apituner.adb_grant import (
    ACCESSIBILITY_SERVICE,
    NOTIFICATION_LISTENER,
    GrantResult,
    merge_colon_list,
    parse_adb_state,
)


def test_merge_colon_list_appends_without_duplicates():
    assert merge_colon_list(None, NOTIFICATION_LISTENER) == NOTIFICATION_LISTENER
    assert merge_colon_list("", NOTIFICATION_LISTENER) == NOTIFICATION_LISTENER
    assert merge_colon_list("null", NOTIFICATION_LISTENER) == NOTIFICATION_LISTENER

    existing = "com.other/.Listener"
    merged = merge_colon_list(existing, NOTIFICATION_LISTENER)
    assert merged == f"{existing}:{NOTIFICATION_LISTENER}"

    again = merge_colon_list(merged, NOTIFICATION_LISTENER)
    assert again == merged


def test_merge_colon_list_preserves_accessibility_peers():
    existing = "com.google.android.marvin.talkback/.TalkBackService"
    merged = merge_colon_list(existing, ACCESSIBILITY_SERVICE)
    assert existing in merged
    assert ACCESSIBILITY_SERVICE in merged
    assert merged.count(":") == 1


def test_parse_adb_state():
    assert parse_adb_state("device") == "device"
    assert parse_adb_state("unauthorized\nTry again") == "unauthorized"
    assert parse_adb_state(
        "error: device unauthorized.\nOtherwise check for a confirmation dialog on your device."
    ) == "unauthorized"
    assert parse_adb_state("offline") == "offline"
    assert parse_adb_state("") == ""


def test_grant_result_success_ignores_accessibility():
    ok = GrantResult(
        overlay=True,
        usage=True,
        notification=True,
        accessibility=False,
        messages=[],
    )
    assert ok.to_dict()["success"] is True

    partial = GrantResult(
        overlay=True,
        usage=False,
        notification=True,
        accessibility=True,
        messages=["usage failed"],
    )
    assert partial.to_dict()["success"] is False


@pytest.mark.asyncio
async def test_grant_requires_adb(monkeypatch: pytest.MonkeyPatch):
    from apituner import adb_grant

    monkeypatch.setattr(adb_grant.shutil, "which", lambda _name: None)
    with pytest.raises(adb_grant.AdbGrantError, match="adb not found"):
        await adb_grant.grant_agent_permissions("192.0.2.10")
