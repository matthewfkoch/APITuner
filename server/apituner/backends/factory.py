"""Construct the right control backend for a tuner's config."""

from __future__ import annotations

from pathlib import Path

from ..models import Tuner
from .base import ControlBackend


def build_backend(
    tuner: Tuner, certs_dir: Path, *, request_timeout: float = 10.0
) -> ControlBackend:
    """Instantiate a backend from a tuner's ControlConfig.

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
    raise ValueError(f"Unknown control backend type: {ctype!r}")
