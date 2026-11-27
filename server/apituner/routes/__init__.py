"""FastAPI route modules for APITuner."""

from __future__ import annotations

from fastapi import Request

from ..config import ConfigStore
from ..tuner_manager import TunerManager


def get_store(request: Request) -> ConfigStore:
    return request.app.state.store


def get_manager(request: Request) -> TunerManager:
    return request.app.state.manager
