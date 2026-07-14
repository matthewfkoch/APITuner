"""Fetch and cache the public Agent APK latest.json manifest."""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_LATEST_URL = (
    "https://github.com/matthewfkoch/APITuner-releases/releases/latest/download/latest.json"
)
CACHE_TTL_SECONDS = 15 * 60


@dataclass
class AgentLatest:
    version_name: str
    version_code: int
    apk_url: str
    sha256: str = ""
    apk_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "versionName": self.version_name,
            "versionCode": self.version_code,
            "apkUrl": self.apk_url,
            "sha256": self.sha256,
            "apkName": self.apk_name,
        }


class AgentUpdateError(Exception):
    """Raised when latest.json or the APK cannot be fetched/verified."""


class AgentLatestCache:
    def __init__(
        self,
        *,
        url: Optional[str] = None,
        ttl_seconds: float = CACHE_TTL_SECONDS,
    ) -> None:
        self.url = url or os.environ.get("APITUNER_AGENT_LATEST_URL", DEFAULT_LATEST_URL)
        self.ttl_seconds = ttl_seconds
        self._cached: Optional[AgentLatest] = None
        self._cached_at: float = 0.0

    def clear(self) -> None:
        self._cached = None
        self._cached_at = 0.0

    async def get(self, *, force: bool = False) -> AgentLatest:
        now = time.monotonic()
        if (
            not force
            and self._cached is not None
            and (now - self._cached_at) < self.ttl_seconds
        ):
            return self._cached
        latest = await fetch_latest(self.url)
        self._cached = latest
        self._cached_at = now
        return latest


async def fetch_latest(url: str) -> AgentLatest:
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": "APITuner-Server"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise AgentUpdateError(f"Failed to fetch latest.json: {exc}") from exc

    if not isinstance(data, dict):
        raise AgentUpdateError("latest.json must be a JSON object")

    try:
        version_code = int(data.get("versionCode"))
    except (TypeError, ValueError) as exc:
        raise AgentUpdateError("latest.json missing valid versionCode") from exc

    version_name = str(data.get("versionName") or "").strip()
    apk_url = str(data.get("apkUrl") or "").strip()
    if not version_name or not apk_url:
        raise AgentUpdateError("latest.json missing versionName or apkUrl")

    return AgentLatest(
        version_name=version_name,
        version_code=version_code,
        apk_url=apk_url,
        sha256=str(data.get("sha256") or "").strip(),
        apk_name=str(data.get("apkName") or "").strip(),
    )


async def download_apk(latest: AgentLatest, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = latest.apk_name or f"apituner-agent-{latest.version_name}.apk"
    # Avoid path traversal from untrusted names.
    name = Path(name).name
    if not name.endswith(".apk"):
        name = f"{name}.apk"
    dest = dest_dir / name

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=15.0),
            follow_redirects=True,
            headers={"User-Agent": "APITuner-Server"},
        ) as client:
            async with client.stream("GET", latest.apk_url) as resp:
                resp.raise_for_status()
                digest = hashlib.sha256()
                with tempfile.NamedTemporaryFile(delete=False, dir=dest_dir, suffix=".apk") as tmp:
                    tmp_path = Path(tmp.name)
                    async for chunk in resp.aiter_bytes(64 * 1024):
                        tmp.write(chunk)
                        digest.update(chunk)
        actual = digest.hexdigest()
        if latest.sha256 and actual.lower() != latest.sha256.lower():
            tmp_path.unlink(missing_ok=True)
            raise AgentUpdateError("APK sha256 mismatch")
        tmp_path.replace(dest)
        return dest
    except AgentUpdateError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AgentUpdateError(f"Failed to download Agent APK: {exc}") from exc


# Process-wide cache used by the FastAPI app.
latest_cache = AgentLatestCache()
