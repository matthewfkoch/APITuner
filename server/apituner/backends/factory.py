"""Construct the right control backend for a tuner's config."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..models import Tuner
from .base import ControlBackend


def build_backend(
    tuner: Tuner, certs_dir: Path, *, request_timeout: float = 10.0
) -> ControlBackend:
    """Instantiate a backend from a tuner's primary ControlConfig.

    Imports are done lazily so an environment missing one backend's optional
    dependency can still use the other.
    """
    ctype = tuner.control.type
    if ctype == "androidtv_remote":
        from .androidtv_remote import AndroidTvRemoteBackend

        return AndroidTvRemoteBackend(tuner, certs_dir)
    if ctype == "http_agent":
        from .http_agent import HttpAgentBackend

        return HttpAgentBackend(tuner, request_timeout=request_timeout)
    if ctype == "firetv_rest":
        from .firetv_rest import FireTvRestBackend

        return FireTvRestBackend(tuner, certs_dir, request_timeout=request_timeout)
    if ctype == "adb":
        from .adb import AdbBackend

        return AdbBackend(tuner, request_timeout=request_timeout)
    raise ValueError(f"Unknown control backend type: {ctype!r}")


def build_keys_backend(
    tuner: Tuner, certs_dir: Path, *, request_timeout: float = 10.0
) -> Optional[ControlBackend]:
    """Instantiate the optional D-pad / keys plane, or None if unset."""
    if tuner.keys_control is None:
        return None
    # Reuse primary factory with a shim tuner so certs stay keyed by tuner.id.
    shim = tuner.model_copy(update={"control": tuner.keys_control})
    return build_backend(shim, certs_dir, request_timeout=request_timeout)
