"""Clear Max / streaming-app profile prompts via encoder OCR (latency-bounded)."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from typing import Awaitable, Callable, Optional

from .preview import grab_preview_jpeg, have_ffmpeg

logger = logging.getLogger(__name__)

# Wall-clock budget for the whole phase (grabs + OCR + key sleeps).
DEFAULT_BUDGET_SECONDS = 6.5
GRAB_TIMEOUT_SECONDS = 2.5
MAX_SELECTS = 2
SELECT_GAP_SECONDS = 0.45
POST_SELECT_SETTLE_SECONDS = 1.8
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

def ocr_image_bytes(jpeg: bytes, *, timeout: float = 0) -> str | None:
    """Return recognized text, or None when the frame could not be read."""
    if not jpeg or not _have_tesseract():
        return None
    try:
        import pytesseract
        from PIL import Image
        import io
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(jpeg))
        # Upscale a bit for TV UI text; grayscale helps tesseract.
        img = img.convert("L")
        return pytesseract.image_to_string(img, timeout=timeout) or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("OCR failed: %s", exc)
        return None


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

    Distinguish an unreadable initial frame from a known, unverified prompt.
    """
    if not has_dpad:
        logger.warning(
            "Who's-watching clear skipped: no D-pad keys backend "
            "(set keys_control to androidtv_remote, firetv_rest, or adb)"
        )
        return "skipped_no_dpad"

    tools_ok = _have_ffmpeg() and _have_tesseract()
    if not tools_ok:
        logger.info("Who's-watching: ffmpeg/tesseract missing — timed DPAD fallback")
        await _timed_selects(send_key, FALLBACK_SELECTS, SELECT_GAP_SECONDS)
        return "timed_fallback"

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.5, float(budget_seconds))
    selects = 0
    saw_prompt = False
    retried_grab = False
    try:
        async with asyncio.timeout_at(deadline):
            while (remaining := deadline - loop.time()) > 0:
                frame = await grab_encoder_frame(
                    stream_url, timeout=min(GRAB_TIMEOUT_SECONDS, remaining)
                )
                if frame is None:
                    if saw_prompt or retried_grab:
                        break
                    retried_grab = True
                    continue

                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                text = await asyncio.to_thread(
                    ocr_image_bytes, frame, timeout=remaining
                )
                if text is None:
                    break
                if not text_looks_like_whos_watching(text):
                    if saw_prompt:
                        logger.info("Who's-watching: prompt cleared after %d select(s)", selects)
                        return "ocr_cleared"
                    return "ocr_absent"

                saw_prompt = True
                if selects >= MAX_SELECTS or loop.time() >= deadline:
                    break
                await send_key("DPAD_CENTER")
                selects += 1
                await asyncio.sleep(POST_SELECT_SETTLE_SECONDS)
    except TimeoutError:
        logger.debug("Who's-watching: OCR deadline reached")

    if saw_prompt:
        logger.warning("Who's-watching: prompt unverified after %d select(s)", selects)
        return "prompt_unverified"
    logger.warning("Who's-watching: encoder frame unreadable, skipping blind Select presses")
    return "grab_failed"
