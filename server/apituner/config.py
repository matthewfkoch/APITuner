"""Persistent JSON configuration store with ADBTuner import/export helpers."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from .models import AppConfig, Channel, TuneConfiguration
from .channels import ChannelValidationError, validate_channel_numbers

# Fields ADBTuner uses in its channel export; we keep parity for drop-in import/export.
_ADBTUNER_CHANNEL_FIELDS = (
    "provider_name",
    "number",
    "name",
    "url",
    "package_name",
    "alternate_package_name",
    "component",
    "action",
    "extra_string",
    "key_macro",
    "compatibility_mode",
    "tvc_guide_stationid",
    "configuration_uuid",
    "source",
)


def _coerce_channel_number(value: Any) -> int | None:
    """Best-effort int coercion for ADBTuner number/sort_order quirks."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize_adbtuner_channel(item: dict[str, Any]) -> dict[str, Any]:
    """Coerce common ADBTuner export quirks into APITuner's channel schema."""
    out = dict(item)

    number = _coerce_channel_number(out.get("number"))
    if number is None:
        number = _coerce_channel_number(out.get("sort_order"))
    if number is not None:
        out["number"] = number

    sid = out.get("tvc_guide_stationid")
    if sid is None or sid == "":
        out["tvc_guide_stationid"] = None
    else:
        out["tvc_guide_stationid"] = str(sid)

    if out.get("alternate_package_name") == "":
        out["alternate_package_name"] = None

    cuuid = out.get("configuration_uuid")
    if cuuid is None or cuuid == "":
        out["configuration_uuid"] = None
    else:
        out["configuration_uuid"] = str(cuuid)

    if "key_macro" in out and out["key_macro"] is not None:
        from .keys import normalize_key_macro

        km = normalize_key_macro(out["key_macro"])
        out["key_macro"] = km or None

    return out


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
        had_device_id = False
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text())
                had_device_id = bool(
                    isinstance(raw, dict)
                    and isinstance(raw.get("options"), dict)
                    and raw["options"].get("hdhr_device_id")
                )
                config = AppConfig.model_validate(raw)
            except (json.JSONDecodeError, ValueError):
                # Corrupt config: back it up rather than lose it, then start fresh.
                backup = self.path.with_suffix(".json.bak")
                self.path.replace(backup)
                config = AppConfig()
        else:
            config = AppConfig()
        # Persist auto-generated HDHR DeviceID so Channels doesn't see a new device.
        if not had_device_id:
            self._config = config
            self.save()
        return config

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._config.model_dump(), indent=2))
            tmp.replace(self.path)

    @property
    def config(self) -> AppConfig:
        return self._config

    def get_configuration(self, uuid: str) -> Optional[TuneConfiguration]:
        with self._lock:
            for cfg in self._config.configurations:
                if cfg.uuid == uuid:
                    return cfg
        return None

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
            imported: list[Channel] = []
            for index, item in enumerate(data):
                if not isinstance(item, dict):
                    raise ChannelValidationError(
                        f"Channel at index {index} must be an object"
                    )
                normalized = normalize_adbtuner_channel(item)
                label = normalized.get("name") or f"index {index}"
                if normalized.get("number") in (None, ""):
                    raise ChannelValidationError(
                        f"Invalid channel '{label}': missing channel number "
                        "(set number, or include sort_order so it can be filled in)"
                    )
                try:
                    imported.append(Channel.model_validate(normalized))
                except ValidationError as exc:
                    msgs = "; ".join(
                        error.get("msg", "invalid") for error in exc.errors()[:3]
                    )
                    raise ChannelValidationError(
                        f"Invalid channel '{label}': {msgs}"
                    ) from exc
            validate_channel_numbers(imported)
            if replace:
                self._config.channels = imported
            else:
                merged = {c.number: c for c in self._config.channels}
                for ch in imported:
                    merged[ch.number] = ch
                self._config.channels = list(merged.values())
            validate_channel_numbers(self._config.channels)
            self.save()
            return len(imported)

    def replace_source_channels(self, source: str, incoming: list[Channel]) -> int:
        """Replace channels whose ``source`` matches; leave all other rows."""
        want = (source or "").strip()
        if not want:
            raise ChannelValidationError("source is required to replace a channel group")
        with self._lock:
            keep = [
                ch
                for ch in self._config.channels
                if (ch.source or "").strip() != want
            ]
            merged = keep + list(incoming)
            validate_channel_numbers(merged)
            self._config.channels = merged
            self.save()
            return len(incoming)

    def export_configurations(self) -> list[dict[str, Any]]:
        with self._lock:
            return [c.model_dump() for c in self._config.configurations]

    def import_configurations(
        self, data: list[dict[str, Any]] | dict[str, Any], *, replace: bool = False
    ) -> int:
        """Import one or more ADBTuner / babsonnexus configuration JSON objects."""
        items: list[dict[str, Any]]
        if isinstance(data, dict):
            items = [data]
        elif isinstance(data, list):
            items = data
        else:
            raise ChannelValidationError("Configurations must be an object or array")

        with self._lock:
            imported: list[TuneConfiguration] = []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ChannelValidationError(
                        f"Configuration at index {index} must be an object"
                    )
                label = item.get("name") or item.get("uuid") or f"index {index}"
                if not item.get("uuid"):
                    raise ChannelValidationError(
                        f"Invalid configuration '{label}': missing uuid"
                    )
                try:
                    imported.append(TuneConfiguration.model_validate(item))
                except ValidationError as exc:
                    msgs = "; ".join(
                        error.get("msg", "invalid") for error in exc.errors()[:3]
                    )
                    raise ChannelValidationError(
                        f"Invalid configuration '{label}': {msgs}"
                    ) from exc

            if replace:
                self._config.configurations = imported
            else:
                merged = {c.uuid: c for c in self._config.configurations}
                for cfg in imported:
                    merged[cfg.uuid] = cfg
                self._config.configurations = list(merged.values())
            self.save()
            return len(imported)
