from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class CacheEntry:
    value: dict[str, Any]
    expires_at: float
    source: str
    fingerprint: str


class TradingMarketDataCache:
    """Bounded disposable in-memory cache with request coalescing; never user authority.

    It holds provider responses only for this process: decision data is not
    written to or read from local files (WP-8.3).
    """

    def __init__(self, *, max_entries: int = 256) -> None:
        self.max_entries = max(1, int(max_entries))
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.RLock()

    @staticmethod
    def key(*parts: object) -> str:
        return ":".join(str(part).strip() for part in parts)

    @staticmethod
    def fingerprint(value: dict[str, Any]) -> str:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get(self, key: str, *, allow_stale: bool = False) -> CacheEntry | None:
        with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at < time.time() and not allow_stale:
                self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return entry

    def put(self, key: str, value: dict[str, Any], *, ttl_seconds: float, source: str) -> CacheEntry:
        entry = CacheEntry(
            value=value,
            expires_at=time.time() + max(0.0, ttl_seconds),
            source=source,
            fingerprint=self.fingerprint(value),
        )
        with self._guard:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return entry

    def get_or_load(
        self,
        key: str,
        loader: Callable[[], dict[str, Any]],
        *,
        ttl_seconds: float,
        source: str,
    ) -> tuple[dict[str, Any], CacheEntry, bool]:
        cached = self.get(key)
        if cached is not None:
            return cached.value, cached, True
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        try:
            with lock:
                cached = self.get(key)
                if cached is not None:
                    return cached.value, cached, True
                value = loader()
                entry = self.put(key, value, ttl_seconds=ttl_seconds, source=source)
                return value, entry, False
        finally:
            with self._guard:
                self._locks.pop(key, None)

    def clear(self) -> None:
        with self._guard:
            self._entries.clear()
            self._locks.clear()
