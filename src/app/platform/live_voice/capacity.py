"""Bounded per-process admission for persistent live-call transports."""
from __future__ import annotations

import threading
from typing import Any


class LiveCallCapacity:
    def __init__(self, maximum: int) -> None:
        limit = int(maximum)
        if limit < 1:
            raise ValueError("live call capacity must be positive")
        self.maximum = limit
        self._active = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._active >= self.maximum:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._active < 1:
                raise RuntimeError("live call capacity release without a holder")
            self._active -= 1

    def snapshot(self) -> dict[str, int | bool]:
        with self._lock:
            active = self._active
        return {
            "maximum": self.maximum,
            "active": active,
            "available": self.maximum - active,
            "saturated": active >= self.maximum,
        }


_CAPACITY: LiveCallCapacity | None = None
_CAPACITY_LOCK = threading.Lock()


def configure_live_call_capacity(maximum: int) -> LiveCallCapacity:
    global _CAPACITY
    with _CAPACITY_LOCK:
        if _CAPACITY is not None:
            current = _CAPACITY.snapshot()
            if current["maximum"] != int(maximum) and current["active"]:
                raise RuntimeError("cannot change live call capacity while calls are active")
        _CAPACITY = LiveCallCapacity(maximum)
        return _CAPACITY


def live_call_capacity() -> LiveCallCapacity:
    global _CAPACITY
    if _CAPACITY is None:
        from app.runtime.config import get_runtime_config

        with _CAPACITY_LOCK:
            if _CAPACITY is None:
                _CAPACITY = LiveCallCapacity(get_runtime_config().live_max_calls)
    return _CAPACITY


def live_call_capacity_snapshot() -> dict[str, Any]:
    return live_call_capacity().snapshot()


def reset_live_call_capacity_for_tests() -> None:
    global _CAPACITY
    with _CAPACITY_LOCK:
        _CAPACITY = None


__all__ = [
    "LiveCallCapacity",
    "configure_live_call_capacity",
    "live_call_capacity",
    "live_call_capacity_snapshot",
    "reset_live_call_capacity_for_tests",
]
