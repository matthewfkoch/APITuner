"""Build dashboard package-coverage report from Agent/ADB app lists."""

from __future__ import annotations

import logging
from typing import Any, Optional

from .backends.base import ControlBackend
from .models import Channel, Tuner
from .packages import package_candidates, package_installed
from .tuner_manager import TunerManager

logger = logging.getLogger(__name__)


async def _packages_for_backend(backend: ControlBackend) -> list[str]:
    if hasattr(backend, "list_apps"):
        try:
            apps = await backend.list_apps()  # type: ignore[attr-defined]
            if isinstance(apps, list):
                pkgs = [
                    str(a.get("packageName"))
                    for a in apps
                    if isinstance(a, dict) and a.get("packageName")
                ]
                if pkgs:
                    return pkgs
        except Exception as exc:  # noqa: BLE001
            logger.debug("list_apps failed: %s", exc)
    try:
        info = await backend.get_info()
        return list(info.packages or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_info for packages failed: %s", exc)
        return []


async def build_package_coverage(
    tuners: list[Tuner],
    channels: list[Channel],
    manager: TunerManager,
) -> dict[str, Any]:
    """Return installed packages per listable tuner and per-channel warnings."""
    tuner_rows: list[dict[str, Any]] = []
    for tuner in tuners:
        if not tuner.enabled:
            continue
        backend = manager.get_backend(tuner)
        can_list = bool(
            getattr(backend.capabilities, "app_list", False)
            or hasattr(backend, "list_apps")
        )
        if not can_list:
            continue
        packages = await _packages_for_backend(backend)
        tuner_rows.append(
            {
                "id": tuner.id,
                "name": tuner.name,
                "packages": sorted(set(packages)),
                "reachable": bool(packages),
            }
        )

    channel_rows: list[dict[str, Any]] = []
    for ch in channels:
        candidates = package_candidates(ch.package_name, ch.alternate_package_name)
        found_on: list[str] = []
        missing_on: list[str] = []
        unknown_on: list[str] = []
        for tr in tuner_rows:
            pkgs = tr["packages"]
            if not pkgs:
                unknown_on.append(tr["name"])
                continue
            if package_installed(pkgs, ch.package_name, ch.alternate_package_name):
                found_on.append(tr["name"])
            else:
                missing_on.append(tr["name"])
        status = "ok"
        if not tuner_rows:
            status = "unknown"
        elif missing_on and not found_on:
            status = "missing"
        elif missing_on:
            status = "partial"
        elif unknown_on and not found_on:
            status = "unknown"
        channel_rows.append(
            {
                "number": ch.number,
                "name": ch.name,
                "package_name": ch.package_name,
                "alternate_package_name": ch.alternate_package_name,
                "candidates": candidates,
                "status": status,
                "found_on": found_on,
                "missing_on": missing_on,
                "unknown_on": unknown_on,
            }
        )

    missing_count = sum(1 for r in channel_rows if r["status"] == "missing")
    partial_count = sum(1 for r in channel_rows if r["status"] == "partial")
    return {
        "tuners": tuner_rows,
        "channels": channel_rows,
        "summary": {
            "listable_tuners": len(tuner_rows),
            "reachable_tuners": sum(1 for t in tuner_rows if t["reachable"]),
            "channels_missing": missing_count,
            "channels_partial": partial_count,
        },
    }
