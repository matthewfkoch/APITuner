"""Pydantic models for APITuner configuration and API payloads."""

from __future__ import annotations

import secrets
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

BackendType = Literal["androidtv_remote", "http_agent"]

# Recommended backend for Google TV / YouTube TV: package-pinned deep links via the Agent.
DEFAULT_BACKEND: BackendType = "http_agent"

# Default network ports per backend.
DEFAULT_AGENT_PORT = 9092
DEFAULT_REMOTE_API_PORT = 6466
DEFAULT_REMOTE_PAIR_PORT = 6467


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _new_hdhr_device_id() -> str:
    """Stable 8-char uppercase hex DeviceID for HDHomeRun emulation."""
    return secrets.token_hex(4).upper()


class ControlConfig(BaseModel):
    """How APITuner talks to a device."""

    type: BackendType = DEFAULT_BACKEND
    host: str
    # Agent: HTTP port (default 9092). Remote: API command port (default 6466).
    port: Optional[int] = None
    # Remote pairing port (default 6467); only used by the androidtv_remote backend.
    pair_port: Optional[int] = None
    # Optional bearer token for the http_agent backend.
    token: Optional[str] = None


class Tuner(BaseModel):
    """A device + its paired HDMI encoder stream."""

    id: str = Field(default_factory=_new_id)
    name: str
    control: ControlConfig
    # URL to the HDMI encoder's MPEG-TS HTTP stream (e.g. http://192.0.2.20:8090/stream0).
    stream_endpoint: str
    enabled: bool = True


class Channel(BaseModel):
    """A tunable channel. Schema mirrors ADBTuner's export for drop-in import."""

    number: int
    name: str
    provider_name: Optional[str] = None
    package_name: str
    alternate_package_name: Optional[str] = None
    # Explicit activity component (required on Android 12+ for some apps).
    component: Optional[str] = None
    # Deep link URI; maps to the intent `data`.
    url: str = ""
    action: str = "android.intent.action.VIEW"
    # Comma-separated key:value intent extras (http_agent backend).
    extra_string: Optional[str] = None
    # Key names sent after launch to clear prompts (androidtv_remote backend only).
    key_macro: Optional[list[str]] = None
    # More aggressive stop/relaunch behavior for finicky apps.
    compatibility_mode: bool = False
    # Gracenote / TVG station id, included in the generated M3U when present.
    tvc_guide_stationid: Optional[str] = None


class GlobalOptions(BaseModel):
    """Tunable behavior shared across all tuners."""

    tune_timeout_seconds: float = 30.0
    wait_for_playback: bool = True
    ready_settle_seconds: float = 1.0
    stop_on_release: bool = False
    keep_apps_running: bool = True
    retry_on_other_tuner: bool = True
    request_timeout: float = 10.0
    stream_mode: Literal["proxy", "redirect"] = "proxy"
    release_grace_seconds: float = 5.0
    stuck_tuner_timeout_seconds: float = 40.0
    tuner_idle_timeout_seconds: float = 60.0

    # HDHomeRun emulation (Channels DVR / Plex / Emby / Jellyfin).
    hdhr_enabled: bool = True
    hdhr_friendly_name: str = "APITuner"
    hdhr_device_id: str = Field(default_factory=_new_hdhr_device_id)
    # When set, clients should use this port for HDHR URLs; None = same as main server.
    hdhr_port: Optional[int] = None
    hdhr_ssdp_enabled: bool = True
    hdhr_udp_discovery_enabled: bool = True

    # XMLTV EPG built from Channels DVR guide + Gracenote StationIDs.
    # Used as the Custom URL guide provider for the HDHomeRun source.
    channels_dvr_url: Optional[str] = None  # e.g. http://192.0.2.30:8089
    xmltv_source_device: str = "M3U-YouTubeTV"
    xmltv_duration_seconds: int = 259200  # 3 days
    xmltv_cache_seconds: float = 900.0

    @field_validator("hdhr_device_id", mode="before")
    @classmethod
    def _normalize_device_id(cls, value: object) -> object:
        if value is None or value == "":
            return _new_hdhr_device_id()
        if isinstance(value, str):
            cleaned = value.strip().upper().replace("-", "")
            if len(cleaned) == 8 and all(c in "0123456789ABCDEF" for c in cleaned):
                return cleaned
            # Persist whatever was given but normalize case/padding when possible.
            if len(cleaned) <= 8 and all(c in "0123456789ABCDEF" for c in cleaned):
                return cleaned.zfill(8)
        return value


class AppConfig(BaseModel):
    """Root persisted configuration document."""

    tuners: list[Tuner] = Field(default_factory=list)
    channels: list[Channel] = Field(default_factory=list)
    options: GlobalOptions = Field(default_factory=GlobalOptions)
