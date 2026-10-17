"""Selected persistence and recovery for derived Companion Activity state.

Checkpoints are recovery accelerators, not evidence. They live outside Memory v2's evidence
log and are always marked at least sensitive. Unconfirmed hysteresis transitions are never
checkpointed. Fresh evidence is reduced immediately after restore.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from typing import Literal, Protocol, cast

from pydantic import Field

from app.persistence.database import PostgresDatabase, default_database

from .contracts import EvidenceProposition, FrozenContract
from .runtime import ActivityRuntimeResult, CompanionActivityRuntime
from .state import CompanionActivityState

ActivityCheckpointReason = Literal[
    "activity_started",
    "objective_established",
    "strategy_changed",
    "major_progress",
    "significant_event",
    "open_loop_changed",
    "user_correction",
    "activity_ended",
    "manual",
]
CheckpointSensitivity = Literal["sensitive", "secret"]


class CompanionActivityCheckpoint(FrozenContract):
    checkpoint_id: str = Field(min_length=1, max_length=240)
    session_id: str = Field(min_length=1, max_length=200)
    activity_id: str = Field(min_length=1, max_length=200)
    character_id: str | None = Field(default=None, max_length=200)
    revision: int = Field(ge=0)
    generation: str | None = Field(default=None, max_length=160)
    reason: ActivityCheckpointReason
    sensitivity: CheckpointSensitivity = "sensitive"
    source_proposition_ids: tuple[str, ...] = ()
    state: CompanionActivityState
    created_at: datetime
    schema_version: str = Field(
        default="companion-activity-checkpoint@1",
        min_length=1,
        max_length=80,
    )


class CheckpointDecision(FrozenContract):
    should_persist: bool
    reason: ActivityCheckpointReason | None = None


class CompanionActivityCheckpointStore(Protocol):
    def save(self, checkpoint: CompanionActivityCheckpoint) -> CompanionActivityCheckpoint: ...

    def latest(
        self,
        session_id: str,
        *,
        activity_id: str | None = None,
    ) -> CompanionActivityCheckpoint | None: ...


class CompanionCheckpointPolicy:
    """Persist meaningful accepted-state boundaries instead of frame-by-frame state."""

    def decide(
        self,
        *,
        before: CompanionActivityState,
        result: ActivityRuntimeResult,
        propositions: tuple[EvidenceProposition, ...],
    ) -> CheckpointDecision:
        after = result.state
        accepted_ids = set(result.processed_proposition_ids)
        accepted = tuple(
            item for item in propositions if item.proposition_id in accepted_ids
        )
        if accepted_ids and not accepted:
            return CheckpointDecision(should_persist=False)
        for change in result.changes:
            if change.authority_source == "user_explicit" and change.previous_value is not None:
                return CheckpointDecision(should_persist=True, reason="user_correction")
        if any(change.field_name == "current_objective" for change in result.changes):
            return CheckpointDecision(should_persist=True, reason="objective_established")
        if len(after.strategy_changes) > len(before.strategy_changes):
            return CheckpointDecision(should_persist=True, reason="strategy_changed")
        if (
            len(after.progress_markers) > len(before.progress_markers)
            and any(_progress_checkpoint_worthy(item) for item in accepted)
        ):
            return CheckpointDecision(should_persist=True, reason="major_progress")
        if (
            len(after.recent_meaningful_events) > len(before.recent_meaningful_events)
            and any(_event_checkpoint_worthy(item) for item in accepted)
        ):
            return CheckpointDecision(should_persist=True, reason="significant_event")
        before_loops = {(item.loop_id, item.status) for item in before.open_loops}
        after_loops = {(item.loop_id, item.status) for item in after.open_loops}
        if before_loops != after_loops:
            return CheckpointDecision(should_persist=True, reason="open_loop_changed")
        if before.revision == 0 and result.changes:
            return CheckpointDecision(should_persist=True, reason="activity_started")
        return CheckpointDecision(should_persist=False)


def _progress_checkpoint_worthy(proposition: EvidenceProposition) -> bool:
    if proposition.predicate != "progress_marker" or proposition.confidence < 0.8:
        return False
    if proposition.source_kind in {"user", "runtime", "telemetry", "system"}:
        return True
    return _external_importance(proposition) >= 0.8 and proposition.confidence >= 0.9


def _event_checkpoint_worthy(proposition: EvidenceProposition) -> bool:
    if proposition.predicate != "meaningful_event" or proposition.confidence < 0.8:
        return False
    if proposition.source_kind in {"user", "runtime", "telemetry", "system"}:
        return True
    return _external_importance(proposition) >= 0.8 and proposition.confidence >= 0.9


def _external_importance(proposition: EvidenceProposition) -> float:
    if not isinstance(proposition.value, dict):
        return 0.0
    try:
        return max(0.0, min(1.0, float(proposition.value.get("importance", 0.0))))
    except (TypeError, ValueError):
        return 0.0


def build_activity_checkpoint(
    *,
    state: CompanionActivityState,
    reason: ActivityCheckpointReason,
    created_at: datetime,
    source_proposition_ids: tuple[str, ...] = (),
    sensitivity: CheckpointSensitivity = "sensitive",
) -> CompanionActivityCheckpoint:
    stable_state = state.model_copy(update={"pending_transitions": ()})
    checkpoint_id = _checkpoint_id(stable_state, reason)
    return CompanionActivityCheckpoint(
        checkpoint_id=checkpoint_id,
        session_id=stable_state.session_id,
        activity_id=stable_state.activity_id,
        character_id=stable_state.character_id,
        revision=stable_state.revision,
        generation=stable_state.generation,
        reason=reason,
        sensitivity=sensitivity,
        source_proposition_ids=tuple(dict.fromkeys(source_proposition_ids)),
        state=stable_state,
        created_at=created_at,
    )


class InMemoryCompanionActivityCheckpointStore:
    def __init__(self, *, maximum_per_session: int = 32) -> None:
        self._lock = threading.Lock()
        self._maximum = max(1, maximum_per_session)
        self._by_session: dict[str, list[CompanionActivityCheckpoint]] = {}

    def save(self, checkpoint: CompanionActivityCheckpoint) -> CompanionActivityCheckpoint:
        with self._lock:
            values = self._by_session.setdefault(checkpoint.session_id, [])
            values = [
                item
                for item in values
                if not (
                    item.activity_id == checkpoint.activity_id
                    and item.revision == checkpoint.revision
                    and item.reason == checkpoint.reason
                )
            ]
            values.append(checkpoint)
            values.sort(key=lambda item: (item.created_at, item.revision, item.checkpoint_id))
            self._by_session[checkpoint.session_id] = values[-self._maximum :]
        return checkpoint

    def latest(
        self,
        session_id: str,
        *,
        activity_id: str | None = None,
    ) -> CompanionActivityCheckpoint | None:
        with self._lock:
            values = list(self._by_session.get(session_id, ()))
        if activity_id is not None:
            values = [item for item in values if item.activity_id == activity_id]
        return values[-1] if values else None


class PostgresCompanionActivityCheckpointStore:
    """Durable checkpoint store separate from the authoritative evidence log."""

    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()

    def save(self, checkpoint: CompanionActivityCheckpoint) -> CompanionActivityCheckpoint:
        payload = json.dumps(
            checkpoint.state.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        sources = json.dumps(list(checkpoint.source_proposition_ids), separators=(",", ":"))
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                INSERT INTO omnix_companion_activity_checkpoints (
                    checkpoint_id, session_id, activity_id, character_id, revision,
                    generation, reason, sensitivity, source_proposition_ids,
                    state_payload, created_at, schema_version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s, %s
                )
                ON CONFLICT (activity_id, revision, reason)
                DO UPDATE SET
                    checkpoint_id = EXCLUDED.checkpoint_id,
                    character_id = EXCLUDED.character_id,
                    generation = EXCLUDED.generation,
                    sensitivity = EXCLUDED.sensitivity,
                    source_proposition_ids = EXCLUDED.source_proposition_ids,
                    state_payload = EXCLUDED.state_payload,
                    created_at = EXCLUDED.created_at,
                    schema_version = EXCLUDED.schema_version
                RETURNING checkpoint_id, session_id, activity_id, character_id, revision,
                          generation, reason, sensitivity, source_proposition_ids,
                          state_payload, created_at, schema_version
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.session_id,
                    checkpoint.activity_id,
                    checkpoint.character_id,
                    checkpoint.revision,
                    checkpoint.generation,
                    checkpoint.reason,
                    checkpoint.sensitivity,
                    sources,
                    payload,
                    checkpoint.created_at,
                    checkpoint.schema_version,
                ),
            ).fetchone()
        return _checkpoint_from_row(row)

    def latest(
        self,
        session_id: str,
        *,
        activity_id: str | None = None,
    ) -> CompanionActivityCheckpoint | None:
        clauses = ["session_id = %s"]
        values: list[object] = [session_id]
        if activity_id is not None:
            clauses.append("activity_id = %s")
            values.append(activity_id)
        with self.database.transaction() as connection:
            row = connection.execute(
                f"""
                SELECT checkpoint_id, session_id, activity_id, character_id, revision,
                       generation, reason, sensitivity, source_proposition_ids,
                       state_payload, created_at, schema_version
                  FROM omnix_companion_activity_checkpoints
                 WHERE {' AND '.join(clauses)}
                 ORDER BY created_at DESC, revision DESC, checkpoint_id DESC
                 LIMIT 1
                """,
                tuple(values),
            ).fetchone()
        return _checkpoint_from_row(row) if row is not None else None


class CompanionActivityRecovery:
    def __init__(self, runtime: CompanionActivityRuntime | None = None) -> None:
        self._runtime = runtime or CompanionActivityRuntime()

    def recover(
        self,
        checkpoint: CompanionActivityCheckpoint,
        fresh_propositions: tuple[EvidenceProposition, ...],
        *,
        now: datetime,
    ) -> ActivityRuntimeResult:
        restored = checkpoint.state.model_copy(update={"pending_transitions": ()})
        return self._runtime.reduce(restored, fresh_propositions, now=now)


def _checkpoint_id(
    state: CompanionActivityState,
    reason: ActivityCheckpointReason,
) -> str:
    material = f"{state.activity_id}\x1f{state.revision}\x1f{reason}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"activity-checkpoint:{digest}"


def _checkpoint_from_row(row) -> CompanionActivityCheckpoint:
    return CompanionActivityCheckpoint(
        checkpoint_id=str(row[0]),
        session_id=str(row[1]),
        activity_id=str(row[2]),
        character_id=str(row[3]) if row[3] is not None else None,
        revision=int(row[4]),
        generation=str(row[5]) if row[5] is not None else None,
        # Stored values; the model validates them on construction.
        reason=cast(ActivityCheckpointReason, str(row[6])),
        sensitivity=cast(CheckpointSensitivity, str(row[7])),
        source_proposition_ids=tuple(str(item) for item in (row[8] or [])),
        state=CompanionActivityState.model_validate(row[9]),
        created_at=row[10],
        schema_version=str(row[11]),
    )


__all__ = [
    "ActivityCheckpointReason",
    "CheckpointDecision",
    "CompanionActivityCheckpoint",
    "CompanionActivityCheckpointStore",
    "CompanionActivityRecovery",
    "CompanionCheckpointPolicy",
    "InMemoryCompanionActivityCheckpointStore",
    "PostgresCompanionActivityCheckpointStore",
    "build_activity_checkpoint",
]
