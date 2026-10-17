"""Bounded in-process serialization for operations on one agent run."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
import threading
from typing import Any, Iterator


@dataclass(slots=True)
class _RunLockEntry:
    lock: Any = field(default_factory=threading.RLock)
    users: int = 0


class RunLockRegistry:
    """Keep independent runs concurrent without evicting a lock in use."""

    def __init__(self, *, max_entries: int = 4096) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._guard = threading.Lock()
        self._entries: OrderedDict[str, _RunLockEntry] = OrderedDict()

    @contextmanager
    def hold(self, run_id: str) -> Iterator[None]:
        key = str(run_id).strip()
        if not key:
            raise ValueError("run_id must not be empty")
        with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = _RunLockEntry()
                self._entries[key] = entry
            entry.users += 1
            self._entries.move_to_end(key)
            self._evict_idle_entries()

        acquired = False
        try:
            entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            with self._guard:
                entry.users -= 1
                if key in self._entries:
                    self._entries.move_to_end(key)
                self._evict_idle_entries()

    @property
    def retained_lock_count(self) -> int:
        with self._guard:
            return len(self._entries)

    def _evict_idle_entries(self) -> None:
        if len(self._entries) <= self._max_entries:
            return
        for key, entry in tuple(self._entries.items()):
            if len(self._entries) <= self._max_entries:
                return
            if entry.users == 0:
                del self._entries[key]
