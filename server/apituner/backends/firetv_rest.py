"""Fire TV Remote HTTP API backend (ADB-free).

Uses the reverse-engineered Fire TV Remote protocol documented by FireTVRest
(https://github.com/SLC-Josh/FireTVRest): HTTPS on port 8080 with pin pairing,
D-pad keys, and package launch. No ADB and no Agent required for App Play.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import httpx

from ..models import DEFAULT_FIRETV_REST_PORT, DEFAULT_FIRETV_WAKE_PORT, Tuner
from .base import (
    BackendNotPaired,
    BackendUnavailable,
    Capabilities,
    ControlBackend,
    DeviceInfo,
    PlaybackState,
)

logger = logging.getLogger(__name__)

# API key sniffed from the official Fire TV Remote mobile app.
_FIRETV_API_KEY = "0987654321"
_USER_AGENT = "okhttp/4.10.0"

# Map common ADBTuner / androidtvremote2 key names → Fire TV REST action names.
_KEY_MAP = {
    "DPAD_LEFT": "dpad_left",
    "DPAD_RIGHT": "dpad_right",
    "DPAD_UP": "dpad_up",
    "DPAD_DOWN": "dpad_down",
    "DPAD_CENTER": "select",
    "ENTER": "select",
    "SELECT": "select",
    "HOME": "home",
    "BACK": "back",
    "MENU": "menu",
    "SLEEP": "sleep",
    "MEDIA_STOP": "home",  # best-effort; no dedicated stop
    "MEDIA_PAUSE": "home",
    "KEYCODE_DPAD_LEFT": "dpad_left",
    "KEYCODE_DPAD_RIGHT": "dpad_right",
    "KEYCODE_DPAD_UP": "dpad_up",
    "KEYCODE_DPAD_DOWN": "dpad_down",
    "KEYCODE_DPAD_CENTER": "select",
    "KEYCODE_ENTER": "select",
    "KEYCODE_HOME": "home",
    "KEYCODE_BACK": "back",
    "KEYCODE_MENU": "menu",
    "KEYCODE_MEDIA_STOP": "home",
    "KEYCODE_MEDIA_PAUSE": "home",
}


class FireTvRestBackend(ControlBackend):
    capabilities = Capabilities(
        keys=True,
        dpad=True,
        current_app=False,
        playback_state=False,
        power=False,
        app_list=False,
        install=False,
    )

    def __init__(self, tuner: Tuner, certs_dir: Path, *, request_timeout: float = 10.0) -> None:
        self._tuner = tuner
        self._host = tuner.control.host
        self._port = tuner.control.port or DEFAULT_FIRETV_REST_PORT
        self._wake_port = DEFAULT_FIRETV_WAKE_PORT
        self._token_path = certs_dir / f"{tuner.id}.firetv_token"
        self._token = (tuner.control.token or "").strip() or self._read_token_file()
        self._timeout = request_timeout
        self._client = httpx.AsyncClient(
            verify=False,
            timeout=request_timeout,
            headers={
                "X-Api-Key": _FIRETV_API_KEY,
                "user-agent": _USER_AGENT,
                "Content-Type": "application/json; charset=utf-8",
            },
        )

    def _read_token_file(self) -> str:
        try:
            if self._token_path.is_file():
                return self._token_path.read_text().strip()
        except OSError:
            pass
        return ""

    def _write_token(self, token: str) -> None:
        self._token = token.strip()
        try:
            self._token_path.write_text(self._token)
        except OSError as exc:
            logger.warning("Could not persist Fire TV token for %s: %s", self._tuner.id, exc)

    @property
    def client_token(self) -> Optional[str]:
        return self._token or None

    def _base(self) -> str:
        return f"https://{self._host}:{self._port}"

    async def _wake(self) -> None:
        url = f"http://{self._host}:{self._wake_port}/apps/FireTVRemote"
        try:
            await self._client.post(url)
        except httpx.HTTPError as exc:
            logger.debug("Fire TV wake failed for %s: %s", self._host, exc)

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            raise BackendNotPaired(f"Tuner {self._tuner.name!r} is not paired (Fire TV REST)")
        return {"X-Client-Token": self._token}

    async def _post(
        self,
        path: str,
        *,
        json: Optional[dict[str, Any]] = None,
        auth: bool = True,
        wake: bool = True,
    ) -> dict[str, Any]:
        if wake:
            await self._wake()
        headers = dict(self._auth_headers()) if auth else {}
        url = f"{self._base()}{path}"
        try:
            resp = await self._client.post(url, headers=headers, json=json)
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"Fire TV REST request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise BackendNotPaired(
                f"Fire TV REST auth failed ({resp.status_code}); re-pair the tuner"
            )
        if resp.status_code >= 400:
            detail = resp.text[:200] if resp.text else resp.reason_phrase
            raise BackendUnavailable(
                f"Fire TV REST error {resp.status_code} on {path}: {detail}"
            )
        try:
            data = resp.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    async def connect(self) -> None:
        if not self._token:
            raise BackendNotPaired(f"Tuner {self._tuner.name!r} is not paired")
        await self._wake()

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        if not self._token:
            return False
        try:
            await self._wake()
            # Reachability only — do not inject keys on health probes.
            await self._client.get(
                f"{self._base()}/",
                headers={"X-Client-Token": self._token},
                timeout=min(5.0, self._timeout),
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    async def get_info(self) -> DeviceInfo:
        return DeviceInfo(
            model="Fire TV",
            manufacturer="Amazon",
            os_version=None,
            sdk_int=None,
            packages=[],
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
        # Protocol only supports package launch (no deep-link evidence).
        target = package
        if not target:
            raise BackendUnavailable("Fire TV REST launch requires a package name")
        await self._post(f"/v1/FireTV/app/{target}", auth=True)

    async def send_key(self, key: str) -> None:
        action = _KEY_MAP.get(key.strip().upper(), key.strip().lower())
        # Allow already-normalized Fire TV action names.
        if action.startswith("keycode_"):
            action = _KEY_MAP.get(action.upper(), action)
        await self._post(f"/v1/FireTV?action={action}", auth=True)

    async def current_app(self) -> Optional[str]:
        return None

    async def playback_state(self) -> PlaybackState:
        return PlaybackState.UNKNOWN

    async def stop(self) -> None:
        try:
            await self.send_key("HOME")
        except Exception:  # noqa: BLE001
            pass

    @property
    def requires_pairing(self) -> bool:
        return True

    async def is_paired(self) -> bool:
        return bool(self._token)

    async def start_pairing(self) -> None:
        await self._wake()
        await self._post(
            "/v1/FireTV/pin/display",
            json={"friendlyName": f"APITuner ({self._tuner.name})"},
            auth=False,
            wake=False,
        )

    async def finish_pairing(self, pin: str) -> None:
        await self._wake()
        data = await self._post(
            "/v1/FireTV/pin/verify",
            json={"pin": pin.strip()},
            auth=False,
            wake=False,
        )
        token = str(data.get("description") or data.get("token") or "").strip()
        if not token:
            raise BackendUnavailable(
                f"Fire TV pin verify did not return a token: {data!r}"
            )
        self._write_token(token)
