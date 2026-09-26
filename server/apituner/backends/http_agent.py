"""HTTP Agent backend: drives the DisplayLauncher-derived APITuner Agent APK.

Recommended for Google TV / YouTube TV / Fire TV. Day-to-day control is ADB-free
(Agent HTTP API). Permissions are normally granted in the Agent Settings UI; on
Fire OS, overlay/usage/notification may require a one-time dashboard ADB grant.
"""

from __future__ import annotations

import logging
from pathlib import Path
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
        dpad=False,  # Agent cannot inject D-pad; use androidtv_remote / firetv_rest for App Play
        current_app=True,
        playback_state=True,
        power=False,
        app_list=True,
        install=True,
    )

    def __init__(self, tuner: Tuner, *, request_timeout: float = 10.0) -> None:
        self._tuner = tuner
        port = tuner.control.port or DEFAULT_AGENT_PORT
        self._base_url = f"http://{tuner.control.host}:{port}"
        headers = {}
        if tuner.control.token:
            headers["X-Auth-Token"] = tuner.control.token
        # Keep-alive off. Agent builds through 0.1.27 leave an unread POST body
        # on the NanoHTTPD socket (/api/stop, and any 401/404). The next request
        # on that socket is parsed as "HTTP verb {}POST". A fresh connection
        # cannot see those leftover bytes. The current Agent also reads every
        # POST body up front; this still protects devices that have not updated.
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=request_timeout,
            limits=httpx.Limits(max_keepalive_connections=0),
        )

    async def connect(self) -> None:
        # HTTP is stateless; nothing to establish.
        return None

    async def close(self) -> None:
        await self._client.aclose()

    def _check_response(self, resp: httpx.Response, path: str) -> None:
        if resp.status_code in (401, 403):
            raise BackendUnavailable(
                f"Agent auth failed ({resp.status_code}) on {path}; check the tuner token"
            )
        if resp.status_code >= 400:
            detail = _agent_error_detail(resp)
            raise BackendUnavailable(
                f"Agent error {resp.status_code} on {path}"
                + (f": {detail}" if detail else "")
            )

    async def _post(
        self,
        path: str,
        json: Optional[dict] = None,
        *,
        content: Optional[bytes] = None,
    ) -> dict[str, Any]:
        try:
            if content is not None:
                resp = await self._client.post(path, content=content)
            else:
                resp = await self._client.post(path, json=json or {})
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"Agent request failed: {exc}") from exc
        self._check_response(resp, path)
        try:
            return resp.json()
        except ValueError:
            return {}

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            resp = await self._client.get(path)
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"Agent request failed: {exc}") from exc
        self._check_response(resp, path)
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
            agent_version_name=data.get("versionName"),
            agent_version_code=_as_int(data.get("versionCode")),
        )

    async def get_live_capabilities(self) -> dict[str, bool]:
        """Permission-aware capabilities reported by the Agent APK."""
        try:
            data = await self._get("/api/info")
        except BackendUnavailable:
            return {}
        caps = data.get("capabilities")
        return caps if isinstance(caps, dict) else {}

    async def launch(
        self,
        *,
        package: str,
        deeplink: Optional[str] = None,
        component: Optional[str] = None,
        action: Optional[str] = None,
        extras: Optional[str] = None,
        clear_task: bool = False,
    ) -> None:
        # Package-only open (App Play adbtuner_open_app): use launcher intent, not VIEW.
        needs_intent = bool(
            (deeplink and str(deeplink).strip())
            or (component and str(component).strip())
            or (extras and str(extras).strip())
            or (
                action
                and action not in (
                    "android.intent.action.VIEW",
                    "android.intent.action.MAIN",
                )
            )
        )
        if not needs_intent and not (deeplink and str(deeplink).strip()):
            data = await self._post("/api/launch", {"packageName": package})
            if data.get("success") is False:
                raise BackendUnavailable(
                    data.get("message")
                    or f"Agent could not open app {package} "
                    "(is it installed on this device?)"
                )
            return

        payload: dict[str, Any] = {"packageName": package}
        if action:
            payload["action"] = action
        if deeplink:
            payload["data"] = deeplink
        if component:
            payload["component"] = component
        if extras:
            payload["extra_string"] = extras
        if clear_task:
            payload["clearTask"] = True
        data = await self._post("/api/launch-intent", payload)
        # Agent returns HTTP 200 with success:false when the intent cannot start.
        if data.get("success") is False:
            raise BackendUnavailable(
                data.get("message") or f"Agent launch failed for {package}"
            )

    async def send_key(self, key: str) -> None:
        data = await self._post("/api/key", {"key": key})
        if data.get("success") is False:
            msg = data.get("message") or f"unsupported key {key}"
            raise BackendUnavailable(
                f"{msg}. Agent Accessibility only supports BACK/HOME/RECENTS — "
                "set Keys / D-pad (androidtv_remote, firetv_rest, or adb) for arrows"
            )

    async def current_app(self) -> Optional[str]:
        try:
            data = await self._get("/api/foreground")
        except BackendUnavailable:
            return None
        return data.get("packageName")

    async def playback_state(self) -> PlaybackState:
        snap, _, _ = await self.playback_snapshot()
        return snap

    async def playback_snapshot(
        self,
    ) -> tuple[PlaybackState, Optional[str], Optional[str]]:
        try:
            data = await self._get("/api/playback")
        except BackendUnavailable:
            return PlaybackState.UNKNOWN, None, None
        if not isinstance(data, dict) or "playing" not in data:
            return PlaybackState.UNKNOWN, None, None
        ps = PlaybackState.PLAYING if data.get("playing") else PlaybackState.IDLE
        pkg = data.get("package")
        title = data.get("title")
        return (
            ps,
            str(pkg) if pkg else None,
            str(title) if title else None,
        )

    async def stop(self) -> None:
        # Empty body on purpose. NanoHTTPD only discards a POST body when the
        # handler calls parseBody. Agent builds through 0.1.27 do not do that
        # for /api/stop, and httpx keeps the connection. A "{}" body is then
        # glued onto the next request line ("HTTP verb {}POST unhandled"),
        # so the deeplink after HOME never launches and the TV stays on the
        # launcher. Content-Length: 0 leaves nothing for that bug to prepend.
        await self._post("/api/stop", content=b"")

    async def agent_permissions(self) -> Optional[dict[str, bool]]:
        """Special-access flags from /api/diagnostics, or None if unavailable."""
        try:
            data = await self._get("/api/diagnostics")
        except BackendUnavailable:
            return None
        perms = data.get("permissions") if isinstance(data, dict) else None
        if not isinstance(perms, dict) or not perms:
            return None
        return {
            "overlay": bool(perms.get("overlay")),
            "usage": bool(perms.get("usage")),
            "notification": bool(perms.get("notification")),
            "accessibility": bool(perms.get("accessibility")),
        }

    async def upload_apk(self, apk_path: str | Path) -> dict[str, Any]:
        """POST a local APK to the Agent's /api/upload-apk (opens Install dialog)."""
        path = Path(apk_path)
        if not path.is_file():
            raise BackendUnavailable(f"APK not found: {path}")
        try:
            with path.open("rb") as fh:
                files = {"file": (path.name, fh, "application/vnd.android.package-archive")}
                resp = await self._client.post(
                    "/api/upload-apk",
                    files=files,
                    timeout=httpx.Timeout(180.0, connect=15.0),
                )
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"Agent APK upload failed: {exc}") from exc
        self._check_response(resp, "/api/upload-apk")
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not data.get("success", False):
            raise BackendUnavailable(data.get("message") or "Agent rejected APK upload")
        return data if isinstance(data, dict) else {"success": True}

    async def list_apps(self) -> list[dict[str, str]]:
        """Convenience for the dashboard's app picker."""
        data = await self._get("/api/apps")
        return data if isinstance(data, list) else []


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _agent_error_detail(resp: httpx.Response) -> str:
    """Best-effort message from an Agent error JSON body."""
    try:
        data = resp.json()
    except ValueError:
        text = (resp.text or "").strip()
        return text[:200] if text else ""
    if not isinstance(data, dict):
        return ""
    msg = data.get("message") or data.get("detail") or data.get("error")
    return str(msg).strip() if msg else ""
