"""Provider-wide scheduling for foreground and background desktop vision work."""
from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

VisionPriority = Literal["foreground", "background"]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class DesktopVisionWork:
    request_id: str
    session_id: str
    capture_generation: str
    client_sequence: int
    priority: VisionPriority
    created_at: float
    expires_at: float
    payload: Any = field(repr=False)
    source_fingerprint: str = ""

    @property
    def coalescing_key(self) -> tuple[str, str]:
        return self.session_id, self.capture_generation

    def is_stale(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass(frozen=True, slots=True)
class DesktopVisionLease:
    lease_id: str
    work: DesktopVisionWork
    claimed_at: float


@dataclass(frozen=True, slots=True)
class DesktopVisionCoordinatorSnapshot:
    active_request_id: str | None
    active_priority: VisionPriority | None
    foreground_pending: int
    background_pending: int
    background_calls_in_window: int
    dropped: int
    coalesced: int
    canceled: int
    stale: int


class DesktopVisionCoordinator:
    """Single-flight priority queue shared by every desktop companion session.

    Foreground work always outranks background work. Background work is bounded by
    a global calls-per-minute budget, minimum start interval, and maximum pending
    queue. Work is coalesced by session and capture generation.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        background_calls_per_minute: int = 6,
        minimum_background_interval_seconds: float = 8.0,
        maximum_background_pending: int = 64,
    ) -> None:
        if background_calls_per_minute < 1:
            raise ValueError("background_calls_per_minute must be positive")
        if minimum_background_interval_seconds < 0:
            raise ValueError("minimum_background_interval_seconds cannot be negative")
        if maximum_background_pending < 1:
            raise ValueError("maximum_background_pending must be positive")
        self._clock = clock
        self._background_calls_per_minute = background_calls_per_minute
        self._minimum_background_interval = minimum_background_interval_seconds
        self._maximum_background_pending = maximum_background_pending
        self._lock = threading.RLock()
        self._foreground: deque[DesktopVisionWork] = deque()
        self._background: dict[tuple[str, str], DesktopVisionWork] = {}
        self._background_order: deque[tuple[str, str]] = deque()
        self._active: DesktopVisionLease | None = None
        self._background_starts: deque[float] = deque()
        self._last_background_start: float | None = None
        self._canceled_generations: set[tuple[str, str]] = set()
        self._dropped = 0
        self._coalesced = 0
        self._canceled = 0
        self._stale = 0

    def submit(
        self,
        *,
        session_id: str,
        capture_generation: str,
        client_sequence: int,
        priority: VisionPriority,
        ttl_seconds: float,
        payload: Any,
        source_fingerprint: str = "",
        request_id: str | None = None,
    ) -> DesktopVisionWork:
        session = session_id.strip()
        generation = capture_generation.strip()
        if not session or not generation:
            raise ValueError("desktop vision work requires session and capture generation")
        if client_sequence < 0:
            raise ValueError("client_sequence cannot be negative")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = self._clock()
        work = DesktopVisionWork(
            request_id=request_id or f"desktop-vision:{uuid.uuid4().hex}",
            session_id=session,
            capture_generation=generation,
            client_sequence=client_sequence,
            priority=priority,
            created_at=now,
            expires_at=now + ttl_seconds,
            payload=payload,
            source_fingerprint=source_fingerprint.strip(),
        )
        with self._lock:
            self._canceled_generations.discard(work.coalescing_key)
            if priority == "foreground":
                self._foreground.append(work)
                return work
            key = work.coalescing_key
            if key in self._background:
                self._background[key] = work
                self._coalesced += 1
            else:
                while len(self._background) >= self._maximum_background_pending:
                    oldest = self._background_order.popleft()
                    if self._background.pop(oldest, None) is not None:
                        self._dropped += 1
                self._background[key] = work
                self._background_order.append(key)
            return work

    def claim_next(self) -> DesktopVisionLease | None:
        with self._lock:
            if self._active is not None:
                return None
            now = self._clock()
            self._prune_stale(now)
            work = self._claim_foreground(now)
            if work is None:
                work = self._claim_background(now)
            if work is None:
                return None
            lease = DesktopVisionLease(
                lease_id=f"desktop-lease:{uuid.uuid4().hex}",
                work=work,
                claimed_at=now,
            )
            self._active = lease
            if work.priority == "background":
                self._background_starts.append(now)
                self._last_background_start = now
            return lease

    def complete(self, lease_id: str) -> DesktopVisionWork:
        with self._lock:
            if self._active is None or self._active.lease_id != lease_id:
                raise RuntimeError("desktop vision lease is not active")
            work = self._active.work
            self._active = None
            return work

    def abandon(self, lease_id: str) -> DesktopVisionWork:
        with self._lock:
            work = self.complete(lease_id)
            self._dropped += 1
            return work

    def cancel_generation(self, *, session_id: str, capture_generation: str) -> None:
        key = session_id.strip(), capture_generation.strip()
        if not all(key):
            return
        with self._lock:
            self._canceled_generations.add(key)
            removed = 0
            if key in self._background:
                del self._background[key]
                removed += 1
            self._background_order = deque(item for item in self._background_order if item != key)
            retained: deque[DesktopVisionWork] = deque()
            for item in self._foreground:
                if item.coalescing_key == key:
                    removed += 1
                else:
                    retained.append(item)
            self._foreground = retained
            self._canceled += removed

    def accepts_result(self, lease: DesktopVisionLease, *, now: float | None = None) -> bool:
        checked_at = self._clock() if now is None else now
        with self._lock:
            return (
                lease.work.coalescing_key not in self._canceled_generations
                and not lease.work.is_stale(checked_at)
                and self._active is not None
                and self._active.lease_id == lease.lease_id
            )

    def next_background_eligible_in(self) -> float:
        with self._lock:
            now = self._clock()
            self._prune_rate_window(now)
            interval_remaining = 0.0
            if self._last_background_start is not None:
                interval_remaining = max(
                    0.0,
                    self._minimum_background_interval - (now - self._last_background_start),
                )
            budget_remaining = 0.0
            if len(self._background_starts) >= self._background_calls_per_minute:
                budget_remaining = max(0.0, 60.0 - (now - self._background_starts[0]))
            return max(interval_remaining, budget_remaining)

    def snapshot(self) -> DesktopVisionCoordinatorSnapshot:
        with self._lock:
            now = self._clock()
            self._prune_rate_window(now)
            return DesktopVisionCoordinatorSnapshot(
                active_request_id=self._active.work.request_id if self._active else None,
                active_priority=self._active.work.priority if self._active else None,
                foreground_pending=len(self._foreground),
                background_pending=len(self._background),
                background_calls_in_window=len(self._background_starts),
                dropped=self._dropped,
                coalesced=self._coalesced,
                canceled=self._canceled,
                stale=self._stale,
            )

    def _claim_foreground(self, now: float) -> DesktopVisionWork | None:
        while self._foreground:
            work = self._foreground.popleft()
            if self._discarded(work, now):
                continue
            return work
        return None

    def _claim_background(self, now: float) -> DesktopVisionWork | None:
        if self.next_background_eligible_in() > 0:
            return None
        while self._background_order:
            key = self._background_order.popleft()
            work = self._background.pop(key, None)
            if work is None or self._discarded(work, now):
                continue
            return work
        return None

    def _discarded(self, work: DesktopVisionWork, now: float) -> bool:
        if work.coalescing_key in self._canceled_generations:
            self._canceled += 1
            return True
        if work.is_stale(now):
            self._stale += 1
            return True
        return False

    def _prune_stale(self, now: float) -> None:
        self._foreground = deque(item for item in self._foreground if not self._record_stale(item, now))
        retained: deque[tuple[str, str]] = deque()
        for key in self._background_order:
            item = self._background.get(key)
            if item is None:
                continue
            if self._record_stale(item, now):
                self._background.pop(key, None)
                continue
            retained.append(key)
        self._background_order = retained

    def _record_stale(self, work: DesktopVisionWork, now: float) -> bool:
        if not work.is_stale(now):
            return False
        self._stale += 1
        return True

    def _prune_rate_window(self, now: float) -> None:
        while self._background_starts and now - self._background_starts[0] >= 60.0:
            self._background_starts.popleft()


__all__ = [
    "DesktopVisionCoordinator",
    "DesktopVisionCoordinatorSnapshot",
    "DesktopVisionLease",
    "DesktopVisionWork",
    "VisionPriority",
]
