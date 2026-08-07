"""Tests for encoder OCR auto-pair PIN extraction and flow."""

from __future__ import annotations

import pytest

from apituner.auto_pair import auto_pair, extract_pairing_pin


def test_extract_androidtv_pin_clean():
    assert extract_pairing_pin("Enter code A1B2C3 on your device", kind="androidtv_remote") == "A1B2C3"


def test_extract_androidtv_pin_spaced():
    assert extract_pairing_pin("PIN: A1 B2 C3", kind="androidtv_remote") == "A1B2C3"
    assert extract_pairing_pin("A 1 B 2 C 3", kind="androidtv_remote") == "A1B2C3"


def test_extract_androidtv_pin_lowercase():
    assert extract_pairing_pin("code a1b2c3 please", kind="androidtv_remote") == "A1B2C3"


def test_extract_androidtv_rejects_short():
    assert extract_pairing_pin("code AB12", kind="androidtv_remote") is None
    assert extract_pairing_pin("", kind="androidtv_remote") is None


def test_extract_androidtv_rejects_code_false_positive():
    assert extract_pairing_pin("PAIRING CODE C0DECA on TV", kind="androidtv_remote") is None
    assert extract_pairing_pin("Enter code\nA1B2C3\n", kind="androidtv_remote") == "A1B2C3"


def test_extract_fire_pin_clean():
    assert extract_pairing_pin("Your PIN is 4821", kind="firetv_rest") == "4821"


def test_extract_fire_pin_spaced_digits():
    assert extract_pairing_pin("4 8 2 1", kind="firetv_rest") == "4821"


def test_extract_fire_pin_ocr_letter_confusion():
    # O/I misread as letters in a digit-only PIN context
    assert extract_pairing_pin("PIN O82I", kind="firetv_rest") == "0821"


def test_extract_fire_rejects_wrong_length():
    assert extract_pairing_pin("123", kind="firetv_rest") is None
    assert extract_pairing_pin("12345", kind="firetv_rest") is None


class FakePairBackend:
    def __init__(self) -> None:
        self.started = False
        self.finished_with: list[str] = []
        self.paired = False
        self.fail_finish = False

    async def start_pairing(self) -> None:
        self.started = True

    async def finish_pairing(self, pin: str) -> None:
        self.finished_with.append(pin)
        if self.fail_finish:
            raise RuntimeError("bad pin")
        self.paired = True

    async def is_paired(self) -> bool:
        return self.paired


@pytest.mark.asyncio
async def test_auto_pair_no_stream():
    be = FakePairBackend()
    result = await auto_pair(be, "", kind="androidtv_remote")
    assert result.success is False
    assert result.reason == "no_stream"
    assert be.started is False


@pytest.mark.asyncio
async def test_auto_pair_success(monkeypatch):
    be = FakePairBackend()
    monkeypatch.setattr("apituner.auto_pair.have_ffmpeg", lambda: True)
    monkeypatch.setattr("apituner.auto_pair._have_tesseract", lambda: True)

    async def frame(url: str, timeout: float = 1.5):
        return b"fake-jpeg"

    monkeypatch.setattr("apituner.auto_pair.grab_encoder_frame", frame)
    monkeypatch.setattr(
        "apituner.auto_pair.ocr_pairing_frame",
        lambda data, kind: "Pairing code A1B2C3",
    )

    result = await auto_pair(
        be,
        "http://192.0.2.1/s",
        kind="androidtv_remote",
        overlay_wait_seconds=0,
        budget_seconds=2.0,
        max_attempts=2,
    )
    assert result.success is True
    assert result.reason == "paired"
    assert result.pin == "A1B2C3"
    assert be.started is True
    assert be.finished_with == ["A1B2C3"]


@pytest.mark.asyncio
async def test_auto_pair_ocr_failed(monkeypatch):
    be = FakePairBackend()
    monkeypatch.setattr("apituner.auto_pair.have_ffmpeg", lambda: True)
    monkeypatch.setattr("apituner.auto_pair._have_tesseract", lambda: True)

    async def frame(url: str, timeout: float = 1.5):
        return b"fake-jpeg"

    monkeypatch.setattr("apituner.auto_pair.grab_encoder_frame", frame)
    monkeypatch.setattr(
        "apituner.auto_pair.ocr_pairing_frame",
        lambda data, kind: "Home screen Amazon Fire",
    )

    result = await auto_pair(
        be,
        "http://192.0.2.1/s",
        kind="firetv_rest",
        overlay_wait_seconds=0,
        budget_seconds=1.0,
        max_attempts=2,
    )
    assert result.success is False
    assert result.reason == "ocr_failed"
    assert result.pairing_started is True
    assert be.started is True
    assert be.finished_with == []


@pytest.mark.asyncio
async def test_auto_pair_finish_failed(monkeypatch):
    be = FakePairBackend()
    be.fail_finish = True
    monkeypatch.setattr("apituner.auto_pair.have_ffmpeg", lambda: True)
    monkeypatch.setattr("apituner.auto_pair._have_tesseract", lambda: True)

    async def frame(url: str, timeout: float = 1.5):
        return b"fake-jpeg"

    monkeypatch.setattr("apituner.auto_pair.grab_encoder_frame", frame)
    monkeypatch.setattr(
        "apituner.auto_pair.ocr_pairing_frame",
        lambda data, kind: "4821",
    )

    result = await auto_pair(
        be,
        "http://192.0.2.1/s",
        kind="firetv_rest",
        overlay_wait_seconds=0,
        budget_seconds=2.0,
        max_attempts=1,
    )
    assert result.success is False
    assert result.reason == "finish_failed"
    assert result.pin == "4821"
    assert result.pairing_started is True
