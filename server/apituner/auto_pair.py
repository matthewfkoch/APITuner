"""Auto-pair Keys backends by OCRing the TV PIN from the HDMI encoder feed."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from dataclasses import dataclass
from typing import Literal, Optional, Protocol

from .preview import have_ffmpeg
from .whos_watching import grab_encoder_frame

logger = logging.getLogger(__name__)

PairKind = Literal["androidtv_remote", "firetv_rest"]

# Wait for the PIN overlay after start_pairing, then poll encoder frames.
OVERLAY_WAIT_SECONDS = 1.5
DEFAULT_BUDGET_SECONDS = 12.0
GRAB_TIMEOUT_SECONDS = 5.0
MAX_ATTEMPTS = 5

# Google TV Remote PINs are typically 6 hex-ish alphanumerics (e.g. A1B2C3).
_ANDROID_PIN_RE = re.compile(r"\b([0-9A-F]{6})\b", re.IGNORECASE)
# Fire TV REST PIN is typically 4 digits.
_FIRE_PIN_RE = re.compile(r"\b(\d{4})\b")

# OCR often invents hex from on-screen words like "CODE" / "PAIRING CODE".
_ANDROID_PIN_FALSE_POSITIVES = frozenset(
    {
        "C0DECA",
        "CODECA",
        "C0DE00",
        "CODE00",
        "PAIRING",
        "AAAAAA",
        "000000",
        "FFFFFF",
    }
)

# Digit confusions common in OCR of large on-screen numerals.
_OCR_DIGIT_MAP = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "Z": "2",
        "S": "5",
        "B": "8",
        "G": "6",
    }
)


class PairingBackend(Protocol):
    async def start_pairing(self) -> None: ...

    async def finish_pairing(self, pin: str) -> None: ...

    async def is_paired(self) -> bool: ...


@dataclass(frozen=True)
class AutoPairResult:
    success: bool
    reason: str
    pin: Optional[str] = None
    hint: str = ""
    pairing_started: bool = False

    def as_dict(self) -> dict:
        return {
            "success": self.success,
            "reason": self.reason,
            "pin": self.pin,
            "hint": self.hint,
            "pairing_started": self.pairing_started,
        }


def _have_tesseract() -> bool:
    return shutil.which("tesseract") is not None


def extract_pairing_pin(ocr_text: str, *, kind: PairKind) -> Optional[str]:
    """Pull a plausible pairing PIN from OCR text for the given backend kind."""
    raw = (ocr_text or "").strip()
    if not raw:
        return None

    if kind == "firetv_rest":
        # Prefer explicit 4-digit tokens; also try OCR-corrected candidate tokens.
        for match in _FIRE_PIN_RE.finditer(raw):
            return match.group(1)
        spaced = re.findall(r"\d", raw)
        if len(spaced) == 4:
            return "".join(spaced)
        # Per-token only so words like "PIN" are not turned into extra digits (I→1).
        for token in re.findall(r"[0-9A-Za-z]{4,6}", raw):
            corrected = token.translate(_OCR_DIGIT_MAP)
            digits = re.sub(r"\D", "", corrected)
            if len(digits) == 4:
                return digits
        return None

    # androidtv_remote — uppercase hex-like 6-char codes; allow spaced groups.
    upper = raw.upper()
    candidates: list[str] = []

    # Prefer a whole line that is exactly the PIN (large on-screen digits).
    for line in upper.splitlines():
        compact = re.sub(r"[^0-9A-F]", "", line)
        if len(compact) == 6:
            candidates.append(compact)

    for match in _ANDROID_PIN_RE.finditer(upper):
        candidates.append(match.group(1).upper())

    spaced = re.findall(r"[0-9A-F]", upper)
    if len(spaced) == 6:
        candidates.append("".join(spaced))

    compact_all = re.sub(r"[^0-9A-F]", "", upper)
    if len(compact_all) == 6:
        candidates.append(compact_all)

    for pin in candidates:
        pin = pin.upper()
        if pin in _ANDROID_PIN_FALSE_POSITIVES:
            continue
        # Reject codes that are just "CODE" + 2 OCR junk chars.
        if pin.startswith("C0DE") or pin.startswith("CODE"):
            continue
        return pin
    return None


def ocr_pairing_frame(jpeg: bytes, *, kind: PairKind) -> str:
    """OCR a frame with PIN-oriented preprocessing and character whitelist."""
    if not jpeg or not _have_tesseract():
        return ""
    try:
        import io

        import pytesseract
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError:
        return ""
    try:
        img = Image.open(io.BytesIO(jpeg)).convert("L")
        w, h = img.size
        # Pairing dialogs are usually centered; crop middle band to reduce noise.
        left = int(w * 0.15)
        right = int(w * 0.85)
        top = int(h * 0.20)
        bottom = int(h * 0.80)
        if right > left and bottom > top:
            img = img.crop((left, top, right, bottom))
        # Upscale for large on-screen digits/letters.
        img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
        img = ImageOps.autocontrast(img)
        img = ImageEnhance.Contrast(img).enhance(1.6)
        whitelist = "0123456789" if kind == "firetv_rest" else "0123456789ABCDEF"
        # Try sparse layout (full dialog) then single-line (large PIN).
        texts: list[str] = []
        for psm in ("6", "7", "11"):
            config = f"--psm {psm} -c tessedit_char_whitelist={whitelist}"
            texts.append(pytesseract.image_to_string(img, config=config) or "")
        return "\n".join(texts)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Pairing OCR failed: %s", exc)
        return ""


async def auto_pair(
    backend: PairingBackend,
    stream_url: str,
    *,
    kind: PairKind,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    overlay_wait_seconds: float = OVERLAY_WAIT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
) -> AutoPairResult:
    """Start pairing, OCR the PIN from the encoder, and finish pairing.

    On OCR/finish failure, pairing may already have been started (PIN still on
    TV); the caller can fall back to manual ``finish_pairing``.
    """
    url = (stream_url or "").strip()
    if not url:
        return AutoPairResult(
            success=False,
            reason="no_stream",
            hint="Set a stream endpoint (HDMI encoder URL) so APITuner can read the PIN.",
        )
    if not have_ffmpeg() or not _have_tesseract():
        return AutoPairResult(
            success=False,
            reason="ocr_unavailable",
            hint="ffmpeg/tesseract missing — enter the PIN manually.",
        )

    try:
        await backend.start_pairing()
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto_pair start_pairing failed: %s", exc)
        return AutoPairResult(
            success=False,
            reason="start_failed",
            hint=f"Failed to start pairing: {exc}",
        )

    if overlay_wait_seconds > 0:
        await asyncio.sleep(overlay_wait_seconds)

    deadline = time.monotonic() + max(1.0, float(budget_seconds))
    attempts = max(1, int(max_attempts))
    last_text = ""

    for i in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        frame = await grab_encoder_frame(
            url, timeout=min(GRAB_TIMEOUT_SECONDS, max(1.0, remaining))
        )
        if frame is None:
            logger.info("auto_pair: frame grab failed (attempt %d/%d)", i + 1, attempts)
            await asyncio.sleep(min(0.4, max(0.0, deadline - time.monotonic())))
            continue

        text = await asyncio.to_thread(ocr_pairing_frame, frame, kind=kind)
        last_text = text or last_text
        pin = extract_pairing_pin(text, kind=kind)
        if not pin:
            logger.debug(
                "auto_pair: no PIN in OCR (attempt %d/%d): %r",
                i + 1,
                attempts,
                (text or "")[:120],
            )
            await asyncio.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
            continue

        try:
            await backend.finish_pairing(pin)
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto_pair finish_pairing(%s) failed: %s", pin, exc)
            return AutoPairResult(
                success=False,
                reason="finish_failed",
                pin=pin,
                pairing_started=True,
                hint=(
                    f"Read PIN {pin} but pairing failed ({exc}). "
                    "Enter the PIN manually, or cancel on the TV and retry."
                ),
            )

        try:
            paired = await backend.is_paired()
        except Exception:  # noqa: BLE001
            paired = True  # finish succeeded; treat as paired if status check fails
        if paired:
            logger.info("auto_pair: paired with PIN %s (%s)", pin, kind)
            return AutoPairResult(
                success=True,
                reason="paired",
                pin=pin,
                pairing_started=True,
                hint="Paired successfully",
            )
        return AutoPairResult(
            success=False,
            reason="not_paired",
            pin=pin,
            pairing_started=True,
            hint="PIN submitted but device still reports unpaired — try manual Pair.",
        )

    snippet = (last_text or "").replace("\n", " ").strip()[:80]
    hint = "Couldn't read the PIN from the encoder — enter it manually."
    if snippet:
        hint += f" (OCR saw: {snippet!r})"
    return AutoPairResult(
        success=False,
        reason="ocr_failed",
        pairing_started=True,
        hint=hint,
    )


__all__ = [
    "AutoPairResult",
    "PairKind",
    "auto_pair",
    "extract_pairing_pin",
    "ocr_pairing_frame",
]
