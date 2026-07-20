"""In-memory ring buffer of recent log lines for diagnostics downloads."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Deque, Optional


class RingBufferHandler(logging.Handler):
    """Keeps the last N formatted log records in memory."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self._capacity = max(1, capacity)
        self._lines: Deque[str] = deque(maxlen=self._capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001
            self.handleError(record)
            return
        with self._lock:
            self._lines.append(msg)

    def lines(self) -> list[str]:
        with self._lock:
            return list(self._lines)

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()


_handler: Optional[RingBufferHandler] = None


def install_log_buffer(
    *,
    capacity: int = 500,
    logger_name: str = "apituner",
) -> RingBufferHandler:
    """Attach a ring buffer to the apituner logger tree (idempotent)."""
    global _handler
    if _handler is not None:
        return _handler

    handler = RingBufferHandler(capacity=capacity)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger(logger_name)
    root.addHandler(handler)
    # Also capture httpx request lines used in tune/stream debugging.
    logging.getLogger("httpx").addHandler(handler)
    _handler = handler
    return handler


def get_recent_logs() -> list[str]:
    if _handler is None:
        return []
    return _handler.lines()
