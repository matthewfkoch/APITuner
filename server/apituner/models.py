"""Pydantic models for APITuner configuration and API payloads."""

from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

BackendType = Literal["androidtv_remote", "http_agent"]

# Default network ports per backend.
DEFAULT_AGENT_PORT = 9092
DEFAULT_REMOTE_API_PORT = 6466
DEFAULT_REMOTE_PAIR_PORT = 6467


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class ControlConfig(BaseModel):
    """How APITuner talks to a device."""

    type: BackendType
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
    # URL to the HDMI encoder's MPEG-TS HTTP stream (e.g. http://192.168.1.41:8090/stream0).
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
    stop_on_release: bool = False
    keep_apps_running: bool = True
    retry_on_other_tuner: bool = True
    request_timeout: float = 10.0
    stream_mode: Literal["proxy", "redirect"] = "proxy"
    release_grace_seconds: float = 5.0
    stuck_tuner_timeout_seconds: float = 40.0
    tuner_idle_timeout_seconds: float = 60.0


class AppConfig(BaseModel):
    """Root persisted configuration document."""

    tuners: list[Tuner] = Field(default_factory=list)
    channels: list[Channel] = Field(default_factory=list)
    options: GlobalOptions = Field(default_factory=GlobalOptions)
