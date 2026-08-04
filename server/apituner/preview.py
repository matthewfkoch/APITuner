"""Dashboard encoder preview: JPEG snapshot and continuous MJPEG via ffmpeg."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

logger = logging.getLogger(__name__)

# Boundary used by ffmpeg ``-f mpjpeg`` output.
MJPEG_BOUNDARY = "ffmpeg"

# Shared live-input flags for HDMI encoder MPEG-TS over HTTP.
_LIVE_INPUT_FLAGS = (
    "-fflags",
    "+genpts+discardcorrupt+nobuffer",
    "-flags",
    "low_delay",
    "-probesize",
    "500000",
    "-analyzeduration",
    "500000",
    "-rw_timeout",
    "8000000",
)


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


async def grab_preview_jpeg(
    stream_url: str,
    *,
    timeout: float = 8.0,
    width: int = 640,
) -> Optional[bytes]:
    """Capture one JPEG from an encoder MPEG-TS URL (live-friendly ffmpeg flags)."""
    if not stream_url or not have_ffmpeg():
        return None
    scale = f"scale={max(160, int(width))}:-2"
    with tempfile.TemporaryDirectory(prefix="apituner-preview-") as tmp:
        out = Path(tmp) / "frame.jpg"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *_LIVE_INPUT_FLAGS,
            "-i",
            stream_url,
            "-an",
            "-frames:v",
            "1",
            "-vf",
            scale,
            "-q:v",
            "5",
            str(out),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logger.debug("preview jpeg timed out for %s", stream_url)
                return None
            if proc.returncode != 0 or not out.is_file():
                err = (stderr or b"").decode("utf-8", errors="replace")[:240]
                logger.debug("preview jpeg failed: %s", err)
                return None
            data = out.read_bytes()
            return data if data else None
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("preview jpeg error: %s", exc)
            return None


async def mjpeg_preview_bytes(
    stream_url: str,
    request: Request,
    *,
    width: int = 640,
    fps: float = 5.0,
) -> AsyncIterator[bytes]:
    """Yield multipart MJPEG from ffmpeg until the client disconnects."""
    if not stream_url or not have_ffmpeg():
        return
    scale = f"scale={max(160, int(width))}:-2"
    rate = max(1.0, min(15.0, float(fps)))
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        *_LIVE_INPUT_FLAGS,
        "-i",
        stream_url,
        "-an",
        "-vf",
        scale,
        "-r",
        str(rate),
        "-f",
        "mpjpeg",
        "-q:v",
        "7",
        "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(16 * 1024), timeout=15.0)
            except asyncio.TimeoutError:
                if proc.returncode is not None:
                    break
                continue
            if not chunk:
                break
            yield chunk
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.communicate()
            except Exception:  # noqa: BLE001
                pass


def jpeg_response(data: bytes) -> Response:
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


def mjpeg_response(stream_url: str, request: Request) -> StreamingResponse:
    return StreamingResponse(
        mjpeg_preview_bytes(stream_url, request),
        media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
