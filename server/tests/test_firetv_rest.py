"""Tests for Fire TV REST key mapping / pairing helpers (no live device)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apituner.backends.firetv_rest import FireTvRestBackend, _KEY_MAP
from apituner.models import ControlConfig, Tuner


def test_key_map_covers_babsonnexus_keys():
    for key in (
        "DPAD_LEFT",
        "DPAD_RIGHT",
        "DPAD_UP",
        "DPAD_DOWN",
        "DPAD_CENTER",
        "HOME",
        "BACK",
        "KEYCODE_DPAD_DOWN",
        "MEDIA_STOP",
    ):
        assert key in _KEY_MAP


@pytest.mark.asyncio
async def test_is_paired_reads_token_file(tmp_path: Path):
    tuner = Tuner(
        id="abc123",
        name="Fire",
        control=ControlConfig(type="firetv_rest", host="192.0.2.10"),
        stream_endpoint="http://192.0.2.11/stream",
    )
    backend = FireTvRestBackend(tuner, tmp_path)
    assert backend.requires_pairing is True
    assert await backend.is_paired() is False
    (tmp_path / "abc123.firetv_token").write_text("tok123")
    backend2 = FireTvRestBackend(tuner, tmp_path)
    assert await backend2.is_paired() is True
    assert backend2.client_token == "tok123"
    await backend.close()
    await backend2.close()


@pytest.mark.asyncio
async def test_is_paired_from_control_token(tmp_path: Path):
    tuner = Tuner(
        id="abc123",
        name="Fire",
        control=ControlConfig(
            type="firetv_rest", host="192.0.2.10", token="from-config"
        ),
        stream_endpoint="http://192.0.2.11/stream",
    )
    backend = FireTvRestBackend(tuner, tmp_path)
    assert await backend.is_paired() is True
    assert backend.client_token == "from-config"
    await backend.close()
