"""Clear Max / streaming-app profile prompts via encoder OCR (latency-bounded)."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from typing import Awaitable, Callable, Optional

from .preview import grab_preview_jpeg, have_ffmpeg

logger = logging.getLogger(__name__)

# Wall-clock budget for the whole phase (grabs + OCR + key sleeps).
DEFAULT_BUDGET_SECONDS = 3.5
GRAB_TIMEOUT_SECONDS = 1.5
MAX_SELECTS = 2
SELECT_GAP_SECONDS = 0.45
FALLBACK_SELECTS = 2

SendKey = Callable[[str], Awaitable[None]]

_PROMPT_RE = re.compile(
    r"(who['\u2019`]?\s*s\s+watching|who\s+is\s+watching|whos\s+watching|"
    r"select\s+a\s+profile|choose\s+a\s+profile|who's\s+watching)",
    re.IGNORECASE,
)


def normalize_ocr_text(text: str) -> str:
    t = (text or "").lower()
    t = t.replace("\u2019", "'").replace("`", "'")
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def text_looks_like_whos_watching(text: str) -> bool:
    return bool(_PROMPT_RE.search(normalize_ocr_text(text)))


def _have_ffmpeg() -> bool:
    return have_ffmpeg()


def _have_tesseract() -> bool:
    return shutil.which("tesseract") is not None


async def grab_encoder_frame(
    stream_url: str,
    *,
    timeout: float = GRAB_TIMEOUT_SECONDS,
) -> Optional[bytes]:
    """Capture one JPEG frame from an MPEG-TS HTTP encoder URL via ffmpeg."""
    return await grab_preview_jpeg(stream_url, timeout=timeout, width=960)

def ocr_image_bytes(jpeg: bytes) -> str:
    """OCR a JPEG/PNG byte blob; returns empty string when tesseract/Pillow unavailable."""
    if not jpeg or not _have_tesseract():
        return ""
    try:
        import pytesseract
        from PIL import Image
        import io
    except ImportError:
        return ""
    try:
        img = Image.open(io.BytesIO(jpeg))
        # Upscale a bit for TV UI text; grayscale helps tesseract.
        img = img.convert("L")
        return pytesseract.image_to_string(img) or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("OCR failed: %s", exc)
        return ""


async def _timed_selects(send_key: SendKey, count: int, gap: float) -> None:
    for i in range(count):
        await send_key("DPAD_CENTER")
        if i + 1 < count:
            await asyncio.sleep(gap)


async def clear_whos_watching_prompt(
    *,
    stream_url: str,
    send_key: SendKey,
    has_dpad: bool,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
) -> str:
    """Attempt to clear a profile / who's-watching prompt.

    Returns a short status token for logs:
    ``ocr_cleared`` | ``ocr_absent`` | ``timed_fallback`` | ``skipped_no_dpad``.
    """
    if not has_dpad:
        logger.warning(
            "Who's-watching clear skipped: no D-pad keys backend "
            "(set keys_control to androidtv_remote, firetv_rest, or adb)"
        )
        return "skipped_no_dpad"

    deadline = time.monotonic() + max(0.5, float(budget_seconds))
    tools_ok = _have_ffmpeg() and _have_tesseract()
    if not tools_ok:
        logger.info("Who's-watching: ffmpeg/tesseract missing — timed DPAD fallback")
        await _timed_selects(send_key, FALLBACK_SELECTS, SELECT_GAP_SECONDS)
        return "timed_fallback"

    selects = 0
    saw_prompt = False
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        frame = await grab_encoder_frame(
            stream_url, timeout=min(GRAB_TIMEOUT_SECONDS, remaining)
        )
        if frame is None:
            logger.info("Who's-watching: frame grab failed — timed DPAD fallback")
            await _timed_selects(send_key, FALLBACK_SELECTS, SELECT_GAP_SECONDS)
            return "timed_fallback"

        text = await asyncio.to_thread(ocr_image_bytes, frame)
        if not text_looks_like_whos_watching(text):
            if saw_prompt:
                logger.info("Who's-watching: prompt cleared via OCR")
                return "ocr_cleared"
            logger.info("Who's-watching: no prompt in frame (fast exit)")
            return "ocr_absent"

        saw_prompt = True
        if selects >= MAX_SELECTS:
            break
        if time.monotonic() >= deadline:
            break
        await send_key("DPAD_CENTER")
        selects += 1
        await asyncio.sleep(min(SELECT_GAP_SECONDS, max(0.0, deadline - time.monotonic())))

    if saw_prompt:
        logger.info("Who's-watching: budget exhausted after %d select(s)", selects)
        return "ocr_cleared" if selects else "timed_fallback"

    # No successful OCR path — still try a quick fallback if we never got text.
    await _timed_selects(send_key, FALLBACK_SELECTS, SELECT_GAP_SECONDS)
    return "timed_fallback"
