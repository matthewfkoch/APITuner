"""Interpret ADBTuner / babsonnexus tune configuration command lists.

Maps shell-style commands onto ControlBackend primitives:
open app, keyevents, sleep, ADB_LOOP. On shell-capable backends (network ADB),
force-stop / am start / input keyevent run as real shell for ADBTuner fidelity.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from .backends.base import ControlBackend
from .models import Channel, TuneConfiguration

logger = logging.getLogger(__name__)

_PKG_PLACEHOLDER = "||TARGET_PACKAGE_NAME||"
_URL_PLACEHOLDER = "||TARGET_URL_OR_IDENTIFIER||"

_SLEEP_RE = re.compile(r"^sleep\s+(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
_KEYEVENT_RE = re.compile(
    r"^input\s+keyevent\s+(\S+)\s*$",
    re.IGNORECASE,
)
_OPEN_APP_RE = re.compile(
    r"^adbtuner_open_app\s+'?([^']+?)'?\s*$",
    re.IGNORECASE,
)
_FORCE_STOP_RE = re.compile(
    r"^am\s+force-stop\s+'?([^']+?)'?\s*$",
    re.IGNORECASE,
)
_AM_START_RE = re.compile(r"^am\s+start\b", re.IGNORECASE)
_SHELLISH_RE = re.compile(
    r"^(input\s+|am\s+|monkey\s+|cmd\s+|settings\s+|pm\s+)",
    re.IGNORECASE,
)


class ConfigInterpreterError(Exception):
    """Raised when a configuration command cannot be executed."""


def url_looks_like_deeplink(url: str) -> bool:
    """True when channel.url is an intent URI rather than an App Play loop index."""
    u = (url or "").strip()
    if not u:
        return False
    if "://" in u:
        return True
    if u.startswith("http:") or u.startswith("https:"):
        return True
    return False


def resolve_app_play_config(
    channel: Channel,
    configurations: list[TuneConfiguration],
) -> Optional[TuneConfiguration]:
    """Return the App Play config for a channel, or None for deep-link / default path.

    Raises ConfigInterpreterError if configuration_uuid is set (and url is not a
    deep link) but no matching configuration is imported.
    """
    uuid = (channel.configuration_uuid or "").strip()
    if not uuid:
        return None
    if url_looks_like_deeplink(channel.url):
        return None
    for cfg in configurations:
        if cfg.uuid == uuid:
            return cfg
    raise ConfigInterpreterError(
        f"Channel {channel.number} ({channel.name}) references "
        f"configuration_uuid {uuid!r}, but that configuration is not imported. "
        "Import it under Configurations first."
    )


def _substitute(text: str, *, package: str, identifier: str) -> str:
    return (
        text.replace(_PKG_PLACEHOLDER, package).replace(_URL_PLACEHOLDER, identifier)
    )


def _normalize_key(raw: str) -> str:
    key = raw.strip()
    if key.upper().startswith("KEYCODE_"):
        key = key[8:]
    # Numeric Android keycodes are uncommon in babsonnexus configs; pass through.
    return key.upper() if not key.isdigit() else key


def _has_shell(backend: ControlBackend) -> bool:
    return bool(getattr(backend.capabilities, "shell", False)) and hasattr(
        backend, "run_shell"
    )


async def run_commands(
    backend: ControlBackend,
    commands: list[Any],
    *,
    package: str,
    identifier: str,
) -> None:
    """Execute a list of ADBTuner configuration commands against a backend."""
    for entry in commands:
        await _run_one(backend, entry, package=package, identifier=identifier)


async def _run_one(
    backend: ControlBackend,
    entry: Any,
    *,
    package: str,
    identifier: str,
) -> None:
    if isinstance(entry, dict) and "ADB_LOOP" in entry:
        loop_spec = entry["ADB_LOOP"]
        if not isinstance(loop_spec, dict):
            raise ConfigInterpreterError(f"Invalid ADB_LOOP entry: {entry!r}")
        iterations = loop_spec.get("iterations", 0)
        if isinstance(iterations, str):
            iterations = _substitute(iterations, package=package, identifier=identifier)
            try:
                iterations = int(float(str(iterations).strip()))
            except (TypeError, ValueError) as exc:
                raise ConfigInterpreterError(
                    f"ADB_LOOP iterations not an int: {iterations!r}"
                ) from exc
        else:
            try:
                iterations = int(iterations)
            except (TypeError, ValueError) as exc:
                raise ConfigInterpreterError(
                    f"ADB_LOOP iterations not an int: {iterations!r}"
                ) from exc
        body = loop_spec.get("commands") or []
        if not isinstance(body, list):
            raise ConfigInterpreterError("ADB_LOOP commands must be a list")
        for _ in range(max(0, iterations)):
            for nested in body:
                await _run_one(
                    backend, nested, package=package, identifier=identifier
                )
        return

    if not isinstance(entry, str):
        raise ConfigInterpreterError(f"Unsupported command entry: {entry!r}")

    cmd = _substitute(entry.strip(), package=package, identifier=identifier)
    if not cmd:
        return

    m = _SLEEP_RE.match(cmd)
    if m:
        await asyncio.sleep(float(m.group(1)))
        return

    m = _OPEN_APP_RE.match(cmd)
    if m:
        pkg = m.group(1).strip().strip("'\"")
        await backend.launch(package=pkg)
        return

    m = _FORCE_STOP_RE.match(cmd)
    if m:
        pkg = m.group(1).strip().strip("'\"")
        force_stop = getattr(backend, "force_stop", None)
        if callable(force_stop):
            await force_stop(pkg)
            return
        if _has_shell(backend):
            await backend.run_shell(f"am force-stop {pkg}")  # type: ignore[attr-defined]
            return
        logger.info(
            "am force-stop mapped to best-effort stop/HOME (no shell): %s",
            pkg,
        )
        try:
            await backend.stop()
        except Exception as exc:  # noqa: BLE001
            logger.debug("best-effort stop after force-stop failed: %s", exc)
        return

    # Shell backends: pass through input/am/monkey for ADBTuner fidelity.
    if _has_shell(backend) and _SHELLISH_RE.match(cmd):
        await backend.run_shell(cmd)  # type: ignore[attr-defined]
        return

    m = _KEYEVENT_RE.match(cmd)
    if m:
        await backend.send_key(_normalize_key(m.group(1)))
        return

    if _AM_START_RE.match(cmd):
        raise ConfigInterpreterError(
            f"Unsupported am start without a shell backend "
            f"(use adb backend or adbtuner_open_app): {cmd}"
        )

    # Bare key name fallback (some configs omit "input keyevent").
    if re.fullmatch(r"[A-Za-z0-9_]+", cmd):
        await backend.send_key(_normalize_key(cmd))
        return

    raise ConfigInterpreterError(f"Unsupported configuration command: {cmd}")
