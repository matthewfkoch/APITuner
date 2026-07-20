"""Build a privacy-conscious diagnostics bundle for support / forum posts."""

from __future__ import annotations

import asyncio
import platform
import sys
import time
from typing import Any

from . import __version__
from .backends.http_agent import HttpAgentBackend
from .config import ConfigStore
from .log_buffer import get_recent_logs
from .models import Tuner
from .tuner_manager import TunerManager


def _redact_control(control: dict[str, Any]) -> dict[str, Any]:
    out = dict(control)
    token = out.get("token")
    if token:
        out["token"] = "***"
    return out


async def _probe_agent(backend: HttpAgentBackend) -> dict[str, Any]:
    """Best-effort live Agent snapshot; never raises."""
    probe: dict[str, Any] = {"reachable": False}
    try:
        probe["reachable"] = bool(await backend.health())
    except Exception as exc:  # noqa: BLE001
        probe["health_error"] = str(exc)
        return probe

    try:
        data = await backend._get("/api/diagnostics")  # noqa: SLF001
        # Older Agents 404 with plain text → _get returns {}. Require a real payload.
        if isinstance(data, dict) and data.get("versionName") is not None:
            probe["agent"] = data
            return probe
    except Exception:  # noqa: BLE001
        pass

    # Older Agents without /api/diagnostics — fall back to info + playback.
    try:
        info = await backend.get_info()
        probe["info"] = {
            "model": info.model,
            "manufacturer": info.manufacturer,
            "os_version": info.os_version,
            "sdk_int": info.sdk_int,
            "version_name": info.agent_version_name,
            "version_code": info.agent_version_code,
        }
        probe["capabilities"] = await backend.get_live_capabilities()
    except Exception as exc:  # noqa: BLE001
        probe["info_error"] = str(exc)
    try:
        fg = await backend._get("/api/foreground")  # noqa: SLF001
        probe["foreground"] = fg
    except Exception:  # noqa: BLE001
        pass
    try:
        pb = await backend._get("/api/playback")  # noqa: SLF001
        probe["playback"] = pb
    except Exception:  # noqa: BLE001
        pass
    return probe


async def build_diagnostics(
    store: ConfigStore,
    manager: TunerManager,
    *,
    probe_timeout: float = 5.0,
) -> dict[str, Any]:
    cfg = store.config
    options = cfg.options
    tuners = list(cfg.tuners)

    status_by_id = {row["id"]: row for row in manager.status()}
    probes: dict[str, Any] = {}

    async def one(tuner: Tuner) -> None:
        row: dict[str, Any] = {
            "id": tuner.id,
            "name": tuner.name,
            "enabled": tuner.enabled,
            "control": _redact_control(tuner.control.model_dump()),
            "stream_endpoint": tuner.stream_endpoint,
            "status": status_by_id.get(tuner.id),
        }
        if tuner.control.type != "http_agent":
            row["probe"] = {"skipped": "not http_agent"}
            probes[tuner.id] = row
            return
        try:
            backend = manager.get_backend(tuner)
            if not isinstance(backend, HttpAgentBackend):
                row["probe"] = {"skipped": "backend type mismatch"}
            else:
                row["probe"] = await asyncio.wait_for(
                    _probe_agent(backend), timeout=probe_timeout
                )
        except Exception as exc:  # noqa: BLE001
            row["probe"] = {"error": str(exc)}
        probes[tuner.id] = row

    await asyncio.gather(*(one(t) for t in tuners))

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "apituner_version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "options": options.model_dump(),
        "channel_count": len(cfg.channels),
        "tuner_count": len(tuners),
        "hdhr": {
            "enabled": options.hdhr_enabled,
            "friendly_name": options.hdhr_friendly_name,
            "device_id": options.hdhr_device_id,
        },
        "tuners": [probes[t.id] for t in tuners],
        "recent_logs": get_recent_logs(),
        "notes": [
            "Tokens are redacted. LAN IPs and encoder URLs may be present.",
            "Channel deeplink lineup is omitted.",
            "Share this file when asking for help on the Channels community forum.",
        ],
    }
