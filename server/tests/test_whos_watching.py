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
async def test_whos_watching_fallback_keys_when_tools_missing(monkeypatch):
    keys: list[str] = []

    async def send(k: str) -> None:
        keys.append(k)

    monkeypatch.setattr("apituner.whos_watching._have_ffmpeg", lambda: False)
    monkeypatch.setattr("apituner.whos_watching._have_tesseract", lambda: False)

    status = await clear_whos_watching_prompt(
        stream_url="http://192.0.2.1/s",
        send_key=send,
        has_dpad=True,
        budget_seconds=2.0,
    )
    assert status == "timed_fallback"
    assert keys == ["DPAD_CENTER", "DPAD_CENTER"]


@pytest.mark.asyncio
async def test_whos_watching_grab_failed_skips_blind_keys(monkeypatch):
    keys: list[str] = []
    grabs = 0

    async def send(k: str) -> None:
        keys.append(k)

    async def no_frame(url: str, timeout: float = 2.5):
        nonlocal grabs
        grabs += 1
        return None

    monkeypatch.setattr("apituner.whos_watching.grab_encoder_frame", no_frame)
    monkeypatch.setattr("apituner.whos_watching._have_ffmpeg", lambda: True)
    monkeypatch.setattr("apituner.whos_watching._have_tesseract", lambda: True)

    status = await clear_whos_watching_prompt(
        stream_url="http://192.0.2.1/s",
        send_key=send,
        has_dpad=True,
        budget_seconds=2.0,
    )
    assert status == "grab_failed"
    assert grabs == 2
    assert keys == []


@pytest.mark.asyncio
async def test_whos_watching_one_click_prompt_clears_with_single_press(monkeypatch):
    keys: list[str] = []
    ocr_texts = iter(["Who's Watching?", "MLS Season Pass - Watch Live"])

    async def send(k: str) -> None:
        keys.append(k)

    async def frame(url: str, timeout: float = 2.5):
        return b"fake-jpeg"

    monkeypatch.setattr("apituner.whos_watching.grab_encoder_frame", frame)
    monkeypatch.setattr("apituner.whos_watching._have_ffmpeg", lambda: True)
    monkeypatch.setattr("apituner.whos_watching._have_tesseract", lambda: True)
    monkeypatch.setattr("apituner.whos_watching.POST_SELECT_SETTLE_SECONDS", 0.01)
    monkeypatch.setattr(
        "apituner.whos_watching.ocr_image_bytes",
        lambda data: next(ocr_texts),
    )

    status = await clear_whos_watching_prompt(
        stream_url="http://192.0.2.1/s",
        send_key=send,
        has_dpad=True,
    )
    assert status == "ocr_cleared"
    assert keys == ["DPAD_CENTER"]


@pytest.mark.asyncio
async def test_whos_watching_two_click_prompt_clears_with_two_presses(monkeypatch):
    keys: list[str] = []
    ocr_texts = iter(["Who's Watching?", "Who's Watching?", "Now Playing HBO"])

    async def send(k: str) -> None:
        keys.append(k)

    async def frame(url: str, timeout: float = 2.5):
        return b"fake-jpeg"

    monkeypatch.setattr("apituner.whos_watching.grab_encoder_frame", frame)
    monkeypatch.setattr("apituner.whos_watching._have_ffmpeg", lambda: True)
    monkeypatch.setattr("apituner.whos_watching._have_tesseract", lambda: True)
    monkeypatch.setattr("apituner.whos_watching.POST_SELECT_SETTLE_SECONDS", 0.01)
    monkeypatch.setattr(
        "apituner.whos_watching.ocr_image_bytes",
        lambda data: next(ocr_texts),
    )

    status = await clear_whos_watching_prompt(
        stream_url="http://192.0.2.1/s",
        send_key=send,
        has_dpad=True,
    )
    assert status == "ocr_cleared"
    assert keys == ["DPAD_CENTER", "DPAD_CENTER"]


@pytest.mark.asyncio
async def test_whos_watching_second_grab_timeout_does_not_fire_fallback(monkeypatch):
    keys: list[str] = []
    frames = iter([b"fake-jpeg", None])

    async def send(k: str) -> None:
        keys.append(k)

    async def frame(url: str, timeout: float = 2.5):
        return next(frames)

    monkeypatch.setattr("apituner.whos_watching.grab_encoder_frame", frame)
    monkeypatch.setattr("apituner.whos_watching._have_ffmpeg", lambda: True)
    monkeypatch.setattr("apituner.whos_watching._have_tesseract", lambda: True)
    monkeypatch.setattr("apituner.whos_watching.POST_SELECT_SETTLE_SECONDS", 0.01)
    monkeypatch.setattr(
        "apituner.whos_watching.ocr_image_bytes",
        lambda data: "Who's Watching?",
    )

    status = await clear_whos_watching_prompt(
        stream_url="http://192.0.2.1/s",
        send_key=send,
        has_dpad=True,
    )
    assert status == "ocr_cleared"
    assert keys == ["DPAD_CENTER"]


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
