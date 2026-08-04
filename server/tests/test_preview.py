"""Tests for encoder preview helpers."""

from __future__ import annotations

from apituner.preview import have_ffmpeg, jpeg_response


def test_have_ffmpeg_is_bool():
    assert isinstance(have_ffmpeg(), bool)


def test_jpeg_response_headers():
    resp = jpeg_response(b"\xff\xd8\xff")
    assert resp.media_type == "image/jpeg"
    assert resp.headers.get("cache-control", "").startswith("no-store")
    assert resp.body == b"\xff\xd8\xff"
