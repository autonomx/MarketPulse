"""PostgreSQL-coordinated TTS admission with realtime-first priority."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Literal

from app.persistence.device_permits import (
    default_device_permit_service,
    device_permit_slot,
)


TtsPriority = Literal["realtime", "preview", "offline"]
_CURRENT: ContextVar[TtsPriority] = ContextVar("omnix_tts_priority", default="realtime")
_DEVICE_PRIORITY = {
    "realtime": "realtime",
    "preview": "interactive",
    "offline": "batch",
}


@contextmanager
def generation_class(priority: TtsPriority) -> Iterator[None]:
    token = _CURRENT.set(priority)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def other_process_priority_pending() -> bool:
    """Report persisted realtime or interactive requests waiting on TTS capacity."""
    service = default_device_permit_service()
    if service is None:
        return False
    return service.has_higher_priority_request("tts", priority="batch")


@contextmanager
def generation_slot() -> Iterator[None]:
    """Hold a process-independent permit for the full provider generation call."""
    with device_permit_slot(
        "tts",
        priority=_DEVICE_PRIORITY[_CURRENT.get()],
        timeout_seconds=30.0,
    ):
        yield


__all__ = [
    "TtsPriority",
    "generation_class",
    "generation_slot",
    "other_process_priority_pending",
]
