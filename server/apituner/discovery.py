"""mDNS/Zeroconf discovery of controllable devices on the LAN."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Service type -> APITuner backend type.
_SERVICE_TYPES = {
    "_androidtvremote2._tcp.local.": "androidtv_remote",
    "_apituner._tcp.local.": "http_agent",
}


async def discover(timeout: float = 5.0) -> list[dict[str, Any]]:
    """Browse the LAN for Android TV Remote devices and APITuner Agents."""
    from zeroconf import ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

    results: dict[str, dict[str, Any]] = {}
    aiozc = AsyncZeroconf()

    async def _resolve(service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        try:
            ok = await info.async_request(aiozc.zeroconf, 3000)
        except Exception:  # noqa: BLE001
            return
        if not ok:
            return
        addresses = info.parsed_scoped_addresses() or []
        if not addresses:
            return
        results[name] = {
            "name": name.split(".")[0],
            "host": addresses[0],
            "port": info.port,
            "backend": _SERVICE_TYPES.get(service_type, "unknown"),
        }

    def _on_change(zeroconf, service_type, name, state_change) -> None:
        if state_change is ServiceStateChange.Added:
            asyncio.ensure_future(_resolve(service_type, name))

    browser = AsyncServiceBrowser(
        aiozc.zeroconf, list(_SERVICE_TYPES.keys()), handlers=[_on_change]
    )
    try:
        await asyncio.sleep(timeout)
    finally:
        await browser.async_cancel()
        await aiozc.async_close()
    return list(results.values())
