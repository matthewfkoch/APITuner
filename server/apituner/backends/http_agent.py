"""HTTP Agent backend: drives the DisplayLauncher-derived APITuner Agent APK.

Secondary backend for devices without the Android TV Remote Service (e.g. Fire
TV) and for app list/install features. All capabilities are ADB-free and rely
on permissions the user grants through the Agent's on-device settings.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from ..models import DEFAULT_AGENT_PORT, Tuner
from .base import (
    BackendUnavailable,
    Capabilities,
    ControlBackend,
    DeviceInfo,
    PlaybackState,
)

logger = logging.getLogger(__name__)


class HttpAgentBackend(ControlBackend):
    capabilities = Capabilities(
        keys=True,  # partial: Accessibility global BACK/HOME/RECENTS only
        current_app=True,
        playback_state=True,
        power=False,
        app_list=True,
        install=True,
    )

    def __init__(self, tuner: Tuner) -> None:
        self._tuner = tuner
        port = tuner.control.port or DEFAULT_AGENT_PORT
        self._base_url = f"http://{tuner.control.host}:{port}"
        headers = {}
        if tuner.control.token:
            headers["X-Auth-Token"] = tuner.control.token
        self._client = httpx.AsyncClient(
            base_url=self._base_url, headers=headers, timeout=15.0
        )

    async def connect(self) -> None:
        # HTTP is stateless; nothing to establish.
        return None

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, json: Optional[dict] = None) -> dict[str, Any]:
        try:
            resp = await self._client.post(path, json=json or {})
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"Agent request failed: {exc}") from exc
        if resp.status_code >= 500:
            raise BackendUnavailable(f"Agent error {resp.status_code} on {path}")
        try:
            return resp.json()
        except ValueError:
            return {}

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            resp = await self._client.get(path)
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"Agent request failed: {exc}") from exc
        try:
            return resp.json()
        except ValueError:
            return {}

    async def health(self) -> bool:
        try:
            data = await self._get("/api/health")
        except BackendUnavailable:
            return False
        return bool(data.get("success", True))

    async def get_info(self) -> DeviceInfo:
        try:
            data = await self._get("/api/info")
        except BackendUnavailable:
            return DeviceInfo()
        packages = data.get("packages") or []
        if not packages:
            # Fall back to the apps list for package enumeration.
            try:
                apps = await self._get("/api/apps")
                if isinstance(apps, list):
                    packages = [a.get("packageName") for a in apps if a.get("packageName")]
            except BackendUnavailable:
                packages = []
        return DeviceInfo(
            model=data.get("model"),
            manufacturer=data.get("manufacturer"),
            os_version=data.get("androidVersion"),
            sdk_int=data.get("sdkInt"),
            packages=packages,
        )

    async def launch(
        self,
        *,
        package: str,
        deeplink: Optional[str] = None,
        component: Optional[str] = None,
        action: Optional[str] = None,
        extras: Optional[str] = None,
    ) -> None:
        payload: dict[str, Any] = {"packageName": package}
        if action:
            payload["action"] = action
        if deeplink:
            payload["data"] = deeplink
        if component:
            payload["component"] = component
        if extras:
            payload["extra_string"] = extras
        await self._post("/api/launch-intent", payload)

    async def send_key(self, key: str) -> None:
        await self._post("/api/key", {"key": key})

    async def current_app(self) -> Optional[str]:
        try:
            data = await self._get("/api/foreground")
        except BackendUnavailable:
            return None
        return data.get("packageName")

    async def playback_state(self) -> PlaybackState:
        try:
            data = await self._get("/api/playback")
        except BackendUnavailable:
            return PlaybackState.UNKNOWN
        if "playing" not in data:
            return PlaybackState.UNKNOWN
        return PlaybackState.PLAYING if data.get("playing") else PlaybackState.IDLE

    async def stop(self) -> None:
        await self._post("/api/stop", {})

    async def list_apps(self) -> list[dict[str, str]]:
        """Convenience for the dashboard's app picker."""
        data = await self._get("/api/apps")
        return data if isinstance(data, list) else []
