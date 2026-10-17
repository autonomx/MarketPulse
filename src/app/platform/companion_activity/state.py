"""Revisable current-state projection for continuous companion activity."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .authority import ActivityAuthoritySource
from .contracts import FrozenContract

OpenLoopStatus = Literal["open", "resolved", "abandoned", "superseded"]


class ActivityField(FrozenContract):
    value: Any
    authority_source: ActivityAuthoritySource
    confidence: float = Field(ge=0.0, le=1.0)
    proposition_ids: tuple[str, ...] = ()
    stable_since: datetime
    updated_at: datetime
    revision: int = Field(default=1, ge=1)
    last_transition_reason: str = Field(min_length=1, max_length=240)


class ActivityTransitionCandidate(FrozenContract):
    field_name: str = Field(min_length=1, max_length=160)
    value: Any
    authority_source: ActivityAuthoritySource
    confidence: float = Field(ge=0.0, le=1.0)
    proposition_ids: tuple[str, ...] = ()
    first_seen_at: datetime
    last_seen_at: datetime
    confirmation_count: int = Field(default=1, ge=1)


class ActivityStateChange(FrozenContract):
    field_name: str
    previous_value: Any | None = None
    new_value: Any
    authority_source: ActivityAuthoritySource
    proposition_ids: tuple[str, ...] = ()
    reason: str
    changed_at: datetime


class ActivityProgressMarker(FrozenContract):
    marker_id: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=1000)
    proposition_ids: tuple[str, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    recorded_at: datetime


class ActivityMeaningfulEvent(FrozenContract):
    event_id: str = Field(min_length=1, max_length=240)
    kind: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    proposition_ids: tuple[str, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    occurred_at: datetime


class ActivityStrategyChange(FrozenContract):
    change_id: str = Field(min_length=1, max_length=240)
    previous_strategy: Any | None = None
    new_strategy: Any
    proposition_ids: tuple[str, ...] = ()
    changed_at: datetime


class ActivityOpenLoop(FrozenContract):
    loop_id: str = Field(min_length=1, max_length=240)
    activity_id: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1200)
    created_from: tuple[str, ...] = ()
    authority_source: ActivityAuthoritySource
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    blocking: bool = False
    opened_at: datetime
    last_referenced_at: datetime
    status: OpenLoopStatus = "open"
    resolution_evidence: tuple[str, ...] = ()


class CompanionActivityState(FrozenContract):
    """Derived, revisable state. Evidence remains authoritative outside this object."""

    activity_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    character_id: str | None = Field(default=None, max_length=200)
    revision: int = Field(default=0, ge=0)
    generation: str | None = Field(default=None, max_length=160)
    fields: dict[str, ActivityField] = Field(default_factory=dict)
    pending_transitions: tuple[ActivityTransitionCandidate, ...] = ()
    progress_markers: tuple[ActivityProgressMarker, ...] = Field(default=(), max_length=64)
    recent_meaningful_events: tuple[ActivityMeaningfulEvent, ...] = Field(
        default=(),
        max_length=32,
    )
    strategy_changes: tuple[ActivityStrategyChange, ...] = Field(default=(), max_length=24)
    blockers: tuple[str, ...] = Field(default=(), max_length=32)
    open_loops: tuple[ActivityOpenLoop, ...] = Field(default=(), max_length=64)
    started_at: datetime
    last_meaningful_change_at: datetime

    def field(self, name: str) -> ActivityField | None:
        return self.fields.get(name)

    def open_loop(self, loop_id: str) -> ActivityOpenLoop | None:
        return next((item for item in self.open_loops if item.loop_id == loop_id), None)


class ActivityReductionResult(FrozenContract):
    state: CompanionActivityState
    changes: tuple[ActivityStateChange, ...] = ()
    ignored_proposition_ids: tuple[str, ...] = ()


def empty_activity_state(
    *,
    activity_id: str,
    session_id: str,
    started_at: datetime,
    character_id: str | None = None,
    generation: str | None = None,
) -> CompanionActivityState:
    return CompanionActivityState(
        activity_id=activity_id,
        session_id=session_id,
        character_id=character_id,
        generation=generation,
        started_at=started_at,
        last_meaningful_change_at=started_at,
    )


__all__ = [
    "ActivityField",
    "ActivityMeaningfulEvent",
    "ActivityOpenLoop",
    "ActivityProgressMarker",
    "ActivityReductionResult",
    "ActivityStateChange",
    "ActivityStrategyChange",
    "ActivityTransitionCandidate",
    "CompanionActivityState",
    "OpenLoopStatus",
    "empty_activity_state",
]
