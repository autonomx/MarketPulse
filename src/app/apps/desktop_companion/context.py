"""Bounded read model for current Desktop Companion context."""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import DesktopObservation


class DesktopCompanionContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    character_id: str | None = None
    observation_id: str
    scene_summary: str = Field(max_length=2200)
    activity_thread: str = Field(max_length=3200)
    importance: float = Field(ge=0.0, le=1.0)
    observed_at: datetime
    observation_count: int = Field(ge=1)


class DesktopCompanionContextStore:
    """Keep a bounded session activity thread without retaining raw frames or OCR text."""

    def __init__(self, *, maximum_summaries: int = 6) -> None:
        self._maximum = max(1, maximum_summaries)
        self._lock = threading.RLock()
        self._summaries: dict[str, deque[str]] = {}
        self._snapshots: dict[str, DesktopCompanionContextSnapshot] = {}

    def record(
        self,
        observation: DesktopObservation,
        *,
        scene_summary: str,
    ) -> DesktopCompanionContextSnapshot:
        summary = " ".join((scene_summary or "").split())[:2200]
        with self._lock:
            values = self._summaries.setdefault(
                observation.session_id,
                deque(maxlen=self._maximum),
            )
            if summary and (not values or values[-1] != summary):
                values.append(summary)
            previous = self._snapshots.get(observation.session_id)
            snapshot = DesktopCompanionContextSnapshot(
                session_id=observation.session_id,
                character_id=observation.character_id,
                observation_id=observation.observation_id,
                scene_summary=summary,
                activity_thread=" | ".join(values)[:3200],
                importance=observation.importance,
                observed_at=observation.observed_at,
                observation_count=(previous.observation_count + 1) if previous else 1,
            )
            self._snapshots[observation.session_id] = snapshot
            return snapshot

    def snapshot(self, session_id: str) -> DesktopCompanionContextSnapshot | None:
        with self._lock:
            return self._snapshots.get(session_id)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._summaries.pop(session_id, None)
            self._snapshots.pop(session_id, None)


_default_context_store: DesktopCompanionContextStore | None = None
_default_context_lock = threading.Lock()


def default_desktop_companion_context_store() -> DesktopCompanionContextStore:
    global _default_context_store
    if _default_context_store is None:
        with _default_context_lock:
            if _default_context_store is None:
                _default_context_store = DesktopCompanionContextStore()
    return _default_context_store


__all__ = [
    "DesktopCompanionContextSnapshot",
    "DesktopCompanionContextStore",
    "default_desktop_companion_context_store",
]
