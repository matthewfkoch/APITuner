"""Auto-pair Keys backends by OCRing the TV PIN from the HDMI encoder feed."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from dataclasses import dataclass
from typing import Literal, Optional, Protocol

from .preview import grab_preview_jpeg, have_ffmpeg

logger = logging.getLogger(__name__)

PairKind = Literal["androidtv_remote", "firetv_rest"]

# Wait for the PIN overlay after start_pairing, then poll encoder frames.
OVERLAY_WAIT_SECONDS = 1.5
DEFAULT_BUDGET_SECONDS = 20.0
GRAB_TIMEOUT_SECONDS = 8.0
MAX_ATTEMPTS = 6
# Higher than who's-watching preview — large spaced PIN digits need detail.
PAIR_FRAME_WIDTH = 1280

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
        "DEVICE",
        "CANCEL",
        "REQUEST",
        "FOLLOW",
        "APITUN",
        "AAAAAA",
        "000000",
        "FFFFFF",
        "ECACCA",
        "DECAEA",
        "F5DECA",
    }
)

# Words / fragments from the pairing dialog — never treat as PIN tokens.
_ANDROID_PIN_NOISE_TOKENS = frozenset(
    {
        "DEVICE",
        "PAIRING",
        "REQUEST",
        "ENTER",
        "FOLLOWING",
        "CODE",
        "YOUR",
        "APITUNER",
        "CONTROL",
        "THIS",
        "CANCEL",
        "THE",
    }
)

# Digit confusions common in OCR of large on-screen numerals (Fire PINs).
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

# Google TV PIN glyphs are often misread when widely spaced (A90A1E → AQYOATE).
# See _ANDROID_GLYPH_OPTIONS / _decode_android_ocr_token.


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


# Single-glyph OCR confusions → possible hex chars (Google TV pairing UI).
_ANDROID_GLYPH_OPTIONS: dict[str, tuple[str, ...]] = {
    "0": ("0",),
    "1": ("1",),
    "2": ("2",),
    "3": ("3",),
    "4": ("4",),
    "5": ("5",),
    "6": ("6",),
    "7": ("7",),
    "8": ("8",),
    "9": ("9",),
    "A": ("A",),
    "B": ("B",),
    "C": ("C",),
    "D": ("D", "0"),
    "E": ("E",),
    "F": ("F",),
    "O": ("0",),
    "Q": ("0", "9"),
    "I": ("1",),
    "L": ("1",),
    "T": ("1",),
    "|": ("1",),
    "Z": ("2",),
    "S": ("5",),
    "Y": ("9",),
    "G": ("6",),
}


def _decode_android_ocr_token(token: str) -> list[str]:
    """Decode a garbled OCR token into 6-char hex PINs via glyph maps + skips."""
    raw = re.sub(r"[^0-9A-Za-z]", "", (token or "")).upper()
    if len(raw) < 6 or raw in _ANDROID_PIN_NOISE_TOKENS:
        return []
    if raw.startswith("CODE") or raw.startswith("C0DE"):
        return []
    if len(raw) == 6 and re.fullmatch(r"[0-9A-F]{6}", raw):
        return [raw]

    results: list[tuple[int, str]] = []

    def rec(i: int, built: str, skip_cost: int) -> None:
        if len(built) == 6:
            if i == len(raw):
                results.append((skip_cost, built))
            return
        if i >= len(raw):
            return
        remaining_in = len(raw) - i
        remaining_out = 6 - len(built)
        if remaining_in < remaining_out:
            return
        # Skip OCR insertion glyphs only (never drop real hex digits).
        # Prefer skipping Y/Q (ambiguous) over O (strong zero).
        if remaining_in > remaining_out and raw[i] not in "0123456789ABCDEF":
            cost = {"Y": 1, "Q": 1, "T": 2, "I": 2, "L": 2, "O": 5}.get(raw[i], 3)
            rec(i + 1, built, skip_cost + cost)
        # Digraph: QO/OQ/OO → single 0.
        if i + 1 < len(raw) and remaining_out >= 1:
            dig = raw[i : i + 2]
            if dig in ("QO", "OQ", "OO", "DQ", "QD"):
                rec(i + 2, built + "0", skip_cost)
        opts = _ANDROID_GLYPH_OPTIONS.get(raw[i])
        if opts:
            for ch in opts:
                rec(i + 1, built + ch, skip_cost)

    rec(0, "", 0)
    results.sort(key=lambda item: (item[0], item[1]))
    seen: set[str] = set()
    unique: list[str] = []
    for _cost, pin in results:
        if pin not in seen:
            seen.add(pin)
            unique.append(pin)
    return unique


def _android_pin_candidates_from_token(token: str) -> list[str]:
    """Expand an OCR token into plausible 6-char hex PIN candidates."""
    raw = re.sub(r"[^0-9A-Za-z]", "", (token or "")).upper()
    if len(raw) < 6 or raw in _ANDROID_PIN_NOISE_TOKENS:
        return []
    if raw.startswith("CODE") or raw.startswith("C0DE"):
        return []
    return _decode_android_ocr_token(raw)


def _rank_android_pin(pin: str, source_token: str, *, decode_rank: int = 50) -> tuple:
    """Lower tuple sorts first — prefer preserving leading/trailing glyphs."""
    tok = re.sub(r"[^0-9A-Z]", "", (source_token or "").upper())
    first_tok = tok[:1]
    last_tok = tok[-1:] if tok else ""
    first_ok = bool(first_tok) and (
        pin[0] == first_tok
        or (first_tok in "OQD" and pin[0] == "0")
        or (first_tok in "IYTL|" and pin[0] == "1")
        or (first_tok == "Y" and pin[0] == "9")
    )
    last_ok = bool(last_tok) and (
        pin[-1] == last_tok
        or (last_tok in "OQD" and pin[-1] == "0")
        or (last_tok in "IYTL|" and pin[-1] == "1")
        or (last_tok == "Y" and pin[-1] == "9")
    )
    # Y in the OCR token almost always means a 9 in the real PIN.
    y_pen = 0 if ("Y" not in tok or "9" in pin) else 1
    # T/I/L in the OCR token almost always means a 1 in the real PIN.
    t_pen = 0 if (not any(c in tok for c in "TIL|") or "1" in pin) else 1
    # Prefer keeping the same number of A's as the source (AQYOATE has two A's).
    a_pen = abs(tok.count("A") - pin.count("A"))
    return (
        0 if first_ok else 1,
        0 if last_ok else 1,
        y_pen,
        t_pen,
        a_pen,
        decode_rank,
        abs(len(tok) - 6),
        pin,
    )


def extract_pairing_pins(
    ocr_text: str, *, kind: PairKind, limit: int = 8
) -> list[str]:
    """Ranked list of plausible pairing PINs from OCR text."""
    raw = (ocr_text or "").strip()
    if not raw:
        return []

    if kind == "firetv_rest":
        pin = extract_pairing_pin(raw, kind=kind)
        return [pin] if pin else []

    upper = raw.upper()
    scored: list[tuple[tuple, str]] = []

    def _add(pin: str, source: str, *, decode_rank: int = 50) -> None:
        pin = pin.upper()
        if len(pin) != 6 or not re.fullmatch(r"[0-9A-F]{6}", pin):
            return
        if pin in _ANDROID_PIN_FALSE_POSITIVES:
            return
        if pin.startswith("C0DE") or pin.startswith("CODE"):
            return
        scored.append((_rank_android_pin(pin, source, decode_rank=decode_rank), pin))

    for line in upper.splitlines():
        compact = re.sub(r"[^0-9A-F]", "", line)
        if len(compact) == 6:
            _add(compact, compact)
        line_tok = re.sub(r"[^0-9A-Z]+", "", line)
        if 6 <= len(line_tok) <= 12 and line_tok not in _ANDROID_PIN_NOISE_TOKENS:
            for idx, cand in enumerate(_android_pin_candidates_from_token(line_tok)):
                _add(cand, line_tok, decode_rank=idx)

    for match in _ANDROID_PIN_RE.finditer(upper):
        _add(match.group(1), match.group(1))

    for token in re.findall(r"[0-9A-Z]{6,12}", upper):
        if token in _ANDROID_PIN_NOISE_TOKENS:
            continue
        if len(token) == 6 and re.fullmatch(r"[0-9A-F]{6}", token):
            _add(token, token)
        else:
            for idx, cand in enumerate(_android_pin_candidates_from_token(token)):
                _add(cand, token, decode_rank=idx)

    spaced = re.findall(r"[0-9A-F]", upper)
    if len(spaced) == 6:
        _add("".join(spaced), "".join(spaced))

    compact_all = re.sub(r"[^0-9A-F]", "", upper)
    if len(compact_all) == 6:
        _add(compact_all, compact_all)

    scored.sort(key=lambda item: item[0])
    out: list[str] = []
    seen: set[str] = set()
    for _, pin in scored:
        if pin not in seen:
            seen.add(pin)
            out.append(pin)
        if len(out) >= limit:
            break
    return out


def extract_pairing_pin(ocr_text: str, *, kind: PairKind) -> Optional[str]:
    """Pull a plausible pairing PIN from OCR text for the given backend kind."""
    if kind == "firetv_rest":
        raw = (ocr_text or "").strip()
        if not raw:
            return None
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

    pins = extract_pairing_pins(ocr_text, kind=kind, limit=1)
    return pins[0] if pins else None


def ocr_pairing_frame(jpeg: bytes, *, kind: PairKind) -> str:
    """OCR a frame with PIN-oriented preprocessing.

    Android TV PINs are large, widely spaced hex glyphs on a dark dialog.
    A hex whitelist turns surrounding English into garbage and hides the real
    code — run unrestricted OCR (and a PIN-band pass) instead.
    """
    if not jpeg or not _have_tesseract():
        return ""
    try:
        import io

        import pytesseract
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError:
        return ""
    try:
        full = Image.open(io.BytesIO(jpeg)).convert("L")
        w, h = full.size
        texts: list[str] = []

        def _run(
            img: Image.Image, *, whitelist: str | None, psms: tuple[str, ...]
        ) -> None:
            work = img
            # Upscale large TV UI glyphs for tesseract.
            if work.width < 1600:
                scale = max(2, (1600 + work.width - 1) // work.width)
                work = work.resize(
                    (work.width * scale, work.height * scale),
                    Image.Resampling.LANCZOS,
                )
            work = ImageOps.autocontrast(work)
            work = ImageEnhance.Contrast(work).enhance(1.6)
            for variant in (work, ImageOps.invert(work)):
                for psm in psms:
                    config = f"--psm {psm}"
                    if whitelist:
                        config += f" -c tessedit_char_whitelist={whitelist}"
                    texts.append(
                        pytesseract.image_to_string(variant, config=config) or ""
                    )

        if kind == "firetv_rest":
            left, right = int(w * 0.15), int(w * 0.85)
            top, bottom = int(h * 0.20), int(h * 0.80)
            band = (
                full.crop((left, top, right, bottom))
                if right > left and bottom > top
                else full
            )
            _run(band, whitelist="0123456789", psms=("6", "7", "11"))
        else:
            # Full dialog (psm 11/4 surface AQYOATE-style tokens).
            _run(full, whitelist=None, psms=("11", "4"))
            # PIN sits below the instruction line — tighter mid/lower band.
            left, right = int(w * 0.10), int(w * 0.90)
            top, bottom = int(h * 0.40), int(h * 0.75)
            if right > left and bottom > top:
                _run(
                    full.crop((left, top, right, bottom)),
                    whitelist=None,
                    psms=("11", "4", "6"),
                )

        return "\n".join(texts)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Pairing OCR failed: %s", exc)
        return ""


def _exc_text(exc: BaseException) -> str:
    text = str(exc).strip()
    if text:
        return text
    return type(exc).__name__


def _pairing_socket_active(backend: PairingBackend) -> bool:
    """Whether the backend still holds an open pairing session.

    Backends without ``pairing_in_progress`` (e.g. Fire TV REST) are assumed
    active when the caller said pairing already started.
    """
    check = getattr(backend, "pairing_in_progress", None)
    if not callable(check):
        return True
    try:
        return bool(check())
    except Exception:  # noqa: BLE001
        return False


async def auto_pair(
    backend: PairingBackend,
    stream_url: str,
    *,
    kind: PairKind,
    already_started: bool = False,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    overlay_wait_seconds: float = OVERLAY_WAIT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
) -> AutoPairResult:
    """Start pairing (unless already started), OCR the PIN, and finish pairing.

    When the dashboard has already called ``start_pairing`` (PIN on TV), pass
    ``already_started=True`` so we do not rebuild the remote / restart pairing —
    that would orphan the session whose PIN is on screen (androidtv_remote).

    If ``already_started`` but the pairing socket is gone (stale TV dialog), we
    start a fresh session before OCR so finish can succeed.

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
            pairing_started=already_started,
        )

    started = False
    if already_started and _pairing_socket_active(backend):
        logger.info("auto_pair: pairing already started — OCR + finish only")
        started = True
    else:
        if already_started:
            logger.info(
                "auto_pair: already_started but pairing socket missing — restarting"
            )
        try:
            await backend.start_pairing()
            started = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "auto_pair start_pairing failed: %s", _exc_text(exc)
            )
            return AutoPairResult(
                success=False,
                reason="start_failed",
                hint=(
                    f"Failed to start pairing: {_exc_text(exc)}. "
                    "Cancel the pairing dialog on the TV, then retry."
                ),
            )
        if overlay_wait_seconds > 0:
            await asyncio.sleep(overlay_wait_seconds)

    deadline = time.monotonic() + max(1.0, float(budget_seconds))
    attempts = max(1, int(max_attempts))
    last_text = ""
    restarted_after_disconnect = False

    for i in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        frame = await grab_preview_jpeg(
            url,
            timeout=min(GRAB_TIMEOUT_SECONDS, max(1.0, remaining)),
            width=PAIR_FRAME_WIDTH,
        )
        if frame is None:
            logger.info("auto_pair: frame grab failed (attempt %d/%d)", i + 1, attempts)
            await asyncio.sleep(min(0.4, max(0.0, deadline - time.monotonic())))
            continue

        text = await asyncio.to_thread(ocr_pairing_frame, frame, kind=kind)
        last_text = text or last_text
        pins = extract_pairing_pins(text, kind=kind)
        if not pins:
            logger.info(
                "auto_pair: no PIN in OCR (attempt %d/%d): %r",
                i + 1,
                attempts,
                (text or "")[:160],
            )
            await asyncio.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
            continue

        auth_rejected = False
        last_pin = pins[0]
        last_finish_err = ""
        finished = False
        for pin in pins:
            last_pin = pin
            try:
                await backend.finish_pairing(pin)
                finished = True
                break
            except Exception as exc:  # noqa: BLE001
                err = _exc_text(exc)
                last_finish_err = err
                logger.warning("auto_pair finish_pairing(%s) failed: %s", pin, err)
                lost = (
                    "disconnect" in err.lower()
                    or "pairing session was lost" in err.lower()
                )
                if lost:
                    break
                # Wrong OCR candidate — try the next ranked PIN on the same frame.
                if "auth" in err.lower() or "invalid" in err.lower() or "pin" in err.lower():
                    auth_rejected = True
                    continue
                # Unknown failure — don't keep guessing.
                break

        if not finished:
            err = last_finish_err
            lost = (
                "disconnect" in err.lower()
                or "pairing session was lost" in err.lower()
            )
            if lost and not restarted_after_disconnect:
                restarted_after_disconnect = True
                logger.info(
                    "auto_pair: finish lost pairing socket — restarting for a fresh PIN"
                )
                try:
                    await backend.start_pairing()
                    started = True
                except Exception as start_exc:  # noqa: BLE001
                    return AutoPairResult(
                        success=False,
                        reason="finish_failed",
                        pin=last_pin,
                        pairing_started=started,
                        hint=(
                            f"Read PIN {last_pin} but pairing failed ({err}); "
                            f"restart also failed ({_exc_text(start_exc)}). "
                            "Cancel on the TV and retry."
                        ),
                    )
                if overlay_wait_seconds > 0:
                    await asyncio.sleep(overlay_wait_seconds)
                continue
            hint_extra = (
                f" Tried OCR candidates {', '.join(pins)}."
                if auth_rejected and len(pins) > 1
                else ""
            )
            return AutoPairResult(
                success=False,
                reason="finish_failed",
                pin=last_pin,
                pairing_started=True,
                hint=(
                    f"Read PIN {last_pin} but pairing failed ({err})."
                    f"{hint_extra} "
                    "Enter the PIN manually, or cancel on the TV and retry."
                ),
            )

        pin = last_pin
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
        pairing_started=started or already_started,
        hint=hint,
    )


__all__ = [
    "AutoPairResult",
    "PairKind",
    "auto_pair",
    "extract_pairing_pin",
    "extract_pairing_pins",
    "ocr_pairing_frame",
]
