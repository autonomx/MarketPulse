"""Bounded session-scoped memory for uncertain desktop observations."""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .models import DesktopObservation, DesktopObservedChange, DesktopObservedValue


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class DesktopSceneMemorySnapshot:
    session_id: str
    capture_generation: str | None
    source_fingerprint: str | None
    current_scene: DesktopObservedValue | None
    recent_observation_ids: tuple[str, ...]
    recent_events: tuple[DesktopObservedChange, ...]
    uncertainties: tuple[str, ...]
    last_observed_at: datetime | None

    def compact_summary(self, *, max_chars: int = 1500) -> str:
        parts: list[str] = []
        if self.current_scene and self.current_scene.value:
            parts.append(
                f"Current visible scene ({self.current_scene.confidence:.2f} confidence): "
                f"{self.current_scene.value}"
            )
        if self.recent_events:
            rendered = "; ".join(
                f"{item.event} ({item.confidence:.2f})" for item in self.recent_events[-6:]
            )
            parts.append(f"Recent visible changes: {rendered}")
        if self.uncertainties:
            parts.append(f"Uncertainty: {'; '.join(self.uncertainties[-4:])}")
        return "\n".join(parts)[: max(0, max_chars)]


@dataclass(slots=True)
class _SessionMemory:
    capture_generation: str | None = None
    source_fingerprint: str | None = None
    current_scene: DesktopObservedValue | None = None
    current_scene_observed_at: datetime | None = None
    observations: deque[DesktopObservation] = field(default_factory=deque)
    events: dict[str, tuple[DesktopObservedChange, datetime]] = field(default_factory=dict)
    event_order: deque[str] = field(default_factory=deque)
    uncertainties: deque[tuple[str, datetime]] = field(default_factory=deque)
    last_observed_at: datetime | None = None


class DesktopSceneMemory:
    """Store short-lived observations without treating them as authoritative facts."""

    def __init__(
        self,
        *,
        maximum_observations: int = 8,
        maximum_events: int = 24,
        scene_ttl_seconds: float = 45.0,
        event_ttl_seconds: float = 90.0,
        uncertainty_ttl_seconds: float = 45.0,
    ) -> None:
        if maximum_observations < 1 or maximum_events < 1:
            raise ValueError("desktop scene memory limits must be positive")
        self._maximum_observations = maximum_observations
        self._maximum_events = maximum_events
        self._scene_ttl = timedelta(seconds=max(0.001, scene_ttl_seconds))
        self._event_ttl = timedelta(seconds=max(0.001, event_ttl_seconds))
        self._uncertainty_ttl = timedelta(seconds=max(0.001, uncertainty_ttl_seconds))
        self._lock = threading.RLock()
        self._sessions: dict[str, _SessionMemory] = {}

    def record(self, observation: DesktopObservation, *, now: datetime | None = None) -> bool:
        checked_at = now or _utcnow()
        if observation.is_stale(checked_at):
            return False
        with self._lock:
            state = self._sessions.setdefault(observation.session_id, _SessionMemory())
            source_changed = (
                state.capture_generation is not None
                and (
                    state.capture_generation != observation.capture_generation
                    or state.source_fingerprint != observation.source_fingerprint
                )
            )
            if source_changed:
                state = _SessionMemory()
                self._sessions[observation.session_id] = state
            state.capture_generation = observation.capture_generation
            state.source_fingerprint = observation.source_fingerprint
            state.last_observed_at = observation.observed_at
            state.observations.append(observation)
            while len(state.observations) > self._maximum_observations:
                state.observations.popleft()

            if observation.current_scene.value:
                current = state.current_scene
                if current is None or observation.current_scene.confidence >= current.confidence or observation.change_kind == "scene_change":
                    state.current_scene = observation.current_scene
                    state.current_scene_observed_at = observation.observed_at

            for change in [*observation.visible_changes, *observation.possible_events]:
                fingerprint = change.fingerprint or change.event.casefold()
                if fingerprint in state.events:
                    previous, _ = state.events[fingerprint]
                    selected = change if change.confidence >= previous.confidence else previous
                    state.events[fingerprint] = (selected, observation.observed_at)
                    continue
                state.events[fingerprint] = (change, observation.observed_at)
                state.event_order.append(fingerprint)
                while len(state.event_order) > self._maximum_events:
                    expired = state.event_order.popleft()
                    state.events.pop(expired, None)

            for uncertainty in observation.uncertainties:
                if uncertainty and not any(value == uncertainty for value, _ in state.uncertainties):
                    state.uncertainties.append((uncertainty, observation.observed_at))
            self._expire(state, checked_at)
            return True

    def snapshot(self, session_id: str, *, now: datetime | None = None) -> DesktopSceneMemorySnapshot:
        checked_at = now or _utcnow()
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return DesktopSceneMemorySnapshot(
                    session_id=session_id,
                    capture_generation=None,
                    source_fingerprint=None,
                    current_scene=None,
                    recent_observation_ids=(),
                    recent_events=(),
                    uncertainties=(),
                    last_observed_at=None,
                )
            self._expire(state, checked_at)
            return DesktopSceneMemorySnapshot(
                session_id=session_id,
                capture_generation=state.capture_generation,
                source_fingerprint=state.source_fingerprint,
                current_scene=state.current_scene,
                recent_observation_ids=tuple(item.observation_id for item in state.observations),
                recent_events=tuple(
                    state.events[key][0] for key in state.event_order if key in state.events
                ),
                uncertainties=tuple(value for value, _ in state.uncertainties),
                last_observed_at=state.last_observed_at,
            )

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def reset_all(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _expire(self, state: _SessionMemory, now: datetime) -> None:
        while state.observations and state.observations[0].is_stale(now):
            state.observations.popleft()
        if (
            state.current_scene_observed_at is not None
            and now - state.current_scene_observed_at >= self._scene_ttl
        ):
            state.current_scene = None
            state.current_scene_observed_at = None
        retained_order: deque[str] = deque()
        for key in state.event_order:
            item = state.events.get(key)
            if item is None:
                continue
            _, observed_at = item
            if now - observed_at >= self._event_ttl:
                state.events.pop(key, None)
            else:
                retained_order.append(key)
        state.event_order = retained_order
        state.uncertainties = deque(
            (value, observed_at)
            for value, observed_at in state.uncertainties
            if now - observed_at < self._uncertainty_ttl
        )


__all__ = ["DesktopSceneMemory", "DesktopSceneMemorySnapshot"]
