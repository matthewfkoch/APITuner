"""Abstract control-backend interface shared by all device backends."""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


class BackendError(Exception):
    """Base class for control-backend errors."""


class BackendNotPaired(BackendError):
    """The backend requires pairing before it can be used."""


class BackendUnavailable(BackendError):
    """The device could not be reached / controlled."""


class PlaybackState(str, enum.Enum):
    PLAYING = "playing"
    PAUSED = "paused"
    IDLE = "idle"
    UNKNOWN = "unknown"


@dataclass
class Capabilities:
    """What a backend can do, so the orchestrator can adapt its tune flow."""

    keys: bool = False
    current_app: bool = False
    playback_state: bool = False
    power: bool = False
    app_list: bool = False
    install: bool = False


@dataclass
class DeviceInfo:
    model: Optional[str] = None
    manufacturer: Optional[str] = None
    os_version: Optional[str] = None
    sdk_int: Optional[int] = None
    packages: list[str] = field(default_factory=list)


class ControlBackend(ABC):
    """One device's control channel. All methods are async and idempotent-ish."""

    #: Static description of this backend's capabilities.
    capabilities: Capabilities = Capabilities()

    @abstractmethod
    async def connect(self) -> None:
        """Establish (or re-establish) the connection. Safe to call repeatedly."""

    @abstractmethod
    async def close(self) -> None:
        """Tear down any persistent connections."""

    @abstractmethod
    async def health(self) -> bool:
        """Return True if the device is reachable/controllable right now."""

    @abstractmethod
    async def get_info(self) -> DeviceInfo:
        """Return device model/OS and (when available) installed packages."""

    @abstractmethod
    async def launch(
        self,
        *,
        package: str,
        deeplink: Optional[str] = None,
        component: Optional[str] = None,
        action: Optional[str] = None,
        extras: Optional[str] = None,
    ) -> None:
        """Launch/deeplink into an app to begin tuning a channel."""

    @abstractmethod
    async def send_key(self, key: str) -> None:
        """Send a single key command (e.g. DPAD_CENTER, BACK, HOME)."""

    @abstractmethod
    async def current_app(self) -> Optional[str]:
        """Return the foreground app package name, or None if unknown."""

    @abstractmethod
    async def playback_state(self) -> PlaybackState:
        """Return the current media playback state."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop playback / return to a neutral state (best effort)."""

    # -- Pairing (only meaningful for backends that require it) --

    @property
    def requires_pairing(self) -> bool:
        return False

    async def is_paired(self) -> bool:
        return True

    async def start_pairing(self) -> None:
        raise NotImplementedError("This backend does not support pairing")

    async def finish_pairing(self, pin: str) -> None:
        raise NotImplementedError("This backend does not support pairing")
