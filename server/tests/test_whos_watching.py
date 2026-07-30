"""Tests for who's-watching OCR helpers and fallback path."""

from __future__ import annotations

import pytest

from apituner.whos_watching import (
    clear_whos_watching_prompt,
    text_looks_like_whos_watching,
)


def test_whos_watching_ocr_detects_phrase():
    assert text_looks_like_whos_watching("Who's Watching?")
    assert text_looks_like_whos_watching("Who’s watching")  # curly apostrophe
    assert text_looks_like_whos_watching("Select a profile")
    assert text_looks_like_whos_watching("CHOOSE A PROFILE to continue")
    assert not text_looks_like_whos_watching("Now Playing HBO")
    assert not text_looks_like_whos_watching("")


@pytest.mark.asyncio
async def test_whos_watching_skipped_without_dpad():
    keys: list[str] = []

    async def send(k: str) -> None:
        keys.append(k)

    status = await clear_whos_watching_prompt(
        stream_url="http://192.0.2.1/s",
        send_key=send,
        has_dpad=False,
    )
    assert status == "skipped_no_dpad"
    assert keys == []


@pytest.mark.asyncio
async def test_whos_watching_fallback_keys(monkeypatch):
    keys: list[str] = []

    async def send(k: str) -> None:
        keys.append(k)

    async def no_frame(url: str, timeout: float = 1.5):
        return None

    monkeypatch.setattr(
        "apituner.whos_watching.grab_encoder_frame", no_frame
    )
    monkeypatch.setattr("apituner.whos_watching._have_ffmpeg", lambda: True)
    monkeypatch.setattr("apituner.whos_watching._have_tesseract", lambda: True)

    status = await clear_whos_watching_prompt(
        stream_url="http://192.0.2.1/s",
        send_key=send,
        has_dpad=True,
        budget_seconds=2.0,
    )
    assert status == "timed_fallback"
    assert keys == ["DPAD_CENTER", "DPAD_CENTER"]


@pytest.mark.asyncio
async def test_whos_watching_ocr_absent_fast_exit(monkeypatch):
    keys: list[str] = []

    async def send(k: str) -> None:
        keys.append(k)

    async def frame(url: str, timeout: float = 1.5):
        return b"fake-jpeg"

    monkeypatch.setattr("apituner.whos_watching.grab_encoder_frame", frame)
    monkeypatch.setattr("apituner.whos_watching._have_ffmpeg", lambda: True)
    monkeypatch.setattr("apituner.whos_watching._have_tesseract", lambda: True)
    monkeypatch.setattr(
        "apituner.whos_watching.ocr_image_bytes",
        lambda data: "Now Playing Live Sports",
    )

    status = await clear_whos_watching_prompt(
        stream_url="http://192.0.2.1/s",
        send_key=send,
        has_dpad=True,
    )
    assert status == "ocr_absent"
    assert keys == []
