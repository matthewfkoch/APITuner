"""Persistent JSON configuration store with ADBTuner import/export helpers."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .models import AppConfig, Channel

# Fields ADBTuner uses in its channel export; we keep parity for drop-in import/export.
_ADBTUNER_CHANNEL_FIELDS = (
    "provider_name",
    "number",
    "name",
    "url",
    "package_name",
    "alternate_package_name",
    "component",
    "compatibility_mode",
    "tvc_guide_stationid",
)


def _default_data_dir() -> Path:
    return Path(os.environ.get("APITUNER_DATA_DIR", "data")).resolve()


class ConfigStore:
    """Thread-safe loader/saver for the APITuner config document."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or _default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "certs").mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "config.json"
        self._lock = threading.RLock()
        self._config = self._load()

    @property
    def certs_dir(self) -> Path:
        return self.data_dir / "certs"

    def _load(self) -> AppConfig:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text())
                return AppConfig.model_validate(raw)
            except (json.JSONDecodeError, ValueError):
                # Corrupt config: back it up rather than lose it, then start fresh.
                backup = self.path.with_suffix(".json.bak")
                self.path.replace(backup)
        return AppConfig()

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._config.model_dump(), indent=2))
            tmp.replace(self.path)

    @property
    def config(self) -> AppConfig:
        return self._config

    # -- Import / export (ADBTuner-compatible channel lists) --

    def export_channels(self) -> list[dict[str, Any]]:
        with self._lock:
            out: list[dict[str, Any]] = []
            for ch in self._config.channels:
                dumped = ch.model_dump()
                out.append({k: dumped.get(k) for k in _ADBTUNER_CHANNEL_FIELDS})
            return out

    def import_channels(self, data: list[dict[str, Any]], *, replace: bool = False) -> int:
        """Import an ADBTuner-style channel list. Returns the number imported."""
        with self._lock:
            imported = [Channel.model_validate(item) for item in data]
            if replace:
                self._config.channels = imported
            else:
                existing = {c.number for c in self._config.channels}
                for ch in imported:
                    if ch.number in existing:
                        # Replace channel with the same number.
                        self._config.channels = [
                            c for c in self._config.channels if c.number != ch.number
                        ]
                    self._config.channels.append(ch)
            self.save()
            return len(imported)
