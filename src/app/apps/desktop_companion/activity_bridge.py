"""Bridge Desktop Companion observations into the revisable Companion Activity Runtime.

Screen-derived semantics remain external, untrusted, sensitive evidence. User-authored
activity statements enter through a separate explicit parser and remain generation-neutral,
so screen-capture lifecycle cannot manufacture or erase user authority. Selected derived
checkpoints are recovery state, not new evidence authority.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.platform.companion_activity.contracts import CognitionResult, CompanionCognition
from app.platform.companion_activity.contracts import EvidenceProposition, FrozenContract
from app.platform.companion_activity.contracts import (
    ActivityCheckpointReason,
    CompanionActivityCheckpointStore,
    CompanionCheckpointPolicy,
    PostgresCompanionActivityCheckpointStore,
    build_activity_checkpoint,
)
from app.platform.companion_activity.contracts import (
    ActivityRuntimeResult,
    CompanionActivityRuntime,
)
from app.platform.companion_activity.contracts import CompanionActivityState, empty_activity_state
from app.platform.companion_activity.contracts import user_activity_propositions

from .models import DesktopObservation, DesktopObservedChange
from .observation import observation_fingerprint, screen_prompt_injection_observed

CheckpointRuntimeStatus = Literal["not_needed", "persisted", "unavailable"]


class DesktopCompanionActivitySnapshot(FrozenContract):
    session_id: str = Field(min_length=1, max_length=200)
    character_id: str | None = Field(default=None, max_length=200)
    capture_generation: str = Field(min_length=1, max_length=160)
    observation_id: str = Field(min_length=1, max_length=160)
    state: CompanionActivityState
    cognition: CognitionResult
    activity_summary: str = Field(default="", max_length=1800)
    processed_proposition_ids: tuple[str, ...] = ()
    ignored_proposition_ids: tuple[str, ...] = ()
    prompt_injection_suppressed: bool = False
    recovered_from_checkpoint: bool = False
    checkpoint_status: CheckpointRuntimeStatus = "not_needed"
    checkpoint_reason: ActivityCheckpointReason | None = None


class CompanionActivityUserTurnUpdate(FrozenContract):
    session_id: str = Field(min_length=1, max_length=200)
    character_id: str | None = Field(default=None, max_length=200)
    message_id: str = Field(min_length=1, max_length=200)
    state: CompanionActivityState
    cognition: CognitionResult
    processed_proposition_ids: tuple[str, ...] = ()
    ignored_proposition_ids: tuple[str, ...] = ()
    recovered_from_checkpoint: bool = False
    checkpoint_status: CheckpointRuntimeStatus = "not_needed"
    checkpoint_reason: ActivityCheckpointReason | None = None


class DesktopCompanionActivityBridge:
    """Session activity projection fed by bounded perception and explicit user evidence."""

    def __init__(
        self,
        *,
        runtime: CompanionActivityRuntime | None = None,
        cognition: CompanionCognition | None = None,
        checkpoint_policy: CompanionCheckpointPolicy | None = None,
        checkpoint_store: CompanionActivityCheckpointStore | None = None,
    ) -> None:
        self._runtime = runtime or CompanionActivityRuntime()
        self._cognition = cognition or CompanionCognition()
        self._checkpoint_policy = checkpoint_policy or CompanionCheckpointPolicy()
        self._checkpoint_store = checkpoint_store or PostgresCompanionActivityCheckpointStore()
        self._lock = threading.RLock()
        self._states: dict[str, CompanionActivityState] = {}
        self._snapshots: dict[str, DesktopCompanionActivitySnapshot] = {}

    def record(self, observation: DesktopObservation) -> DesktopCompanionActivitySnapshot:
        with self._lock:
            before, recovered, checkpoint_available = self._state_for(observation)
            injection = _observation_contains_prompt_injection(observation)
            propositions = () if injection else _evidence_from_observation(observation, before)
            result = self._runtime.reduce(
                before,
                propositions,
                now=observation.observed_at,
            )
            cognition = self._cognition.evaluate(
                before=before,
                activity_result=result,
                propositions=propositions,
                now=observation.observed_at,
            )
            checkpoint_reason, checkpoint_status, checkpoint_available = self._checkpoint(
                before=before,
                result=result,
                propositions=propositions,
                created_at=observation.observed_at,
                checkpoint_available=checkpoint_available,
            )

            self._states[observation.session_id] = result.state
            snapshot = DesktopCompanionActivitySnapshot(
                session_id=observation.session_id,
                character_id=observation.character_id,
                capture_generation=observation.capture_generation,
                observation_id=observation.observation_id,
                state=result.state,
                cognition=cognition,
                activity_summary=_activity_summary(result.state),
                processed_proposition_ids=result.processed_proposition_ids,
                ignored_proposition_ids=result.ignored_proposition_ids,
                prompt_injection_suppressed=injection,
                recovered_from_checkpoint=recovered,
                checkpoint_status=(
                    checkpoint_status if checkpoint_available else "unavailable"
                ),
                checkpoint_reason=checkpoint_reason,
            )
            self._snapshots[observation.session_id] = snapshot
            return snapshot

    def record_user_turn(
        self,
        *,
        session_id: str,
        character_id: str | None,
        message_id: str,
        content: str,
        observed_at: datetime,
    ) -> CompanionActivityUserTurnUpdate | None:
        """Apply only explicit user-authored activity claims from an accepted Chat turn.

        User evidence is generation-neutral. It can establish or correct durable semantic
        intent while Desktop sharing is stopped, and later binds to the next capture
        generation without inheriting stale screen evidence.
        """

        with self._lock:
            before, recovered, checkpoint_available = self._state_for_user_turn(
                session_id=session_id,
                character_id=character_id,
                observed_at=observed_at,
            )
            propositions = user_activity_propositions(
                session_id=session_id,
                subject=before.activity_id,
                message_id=message_id,
                content=content,
                observed_at=observed_at,
            )
            if not propositions:
                return None
            result = self._runtime.reduce(before, propositions, now=observed_at)
            cognition = self._cognition.evaluate(
                before=before,
                activity_result=result,
                propositions=propositions,
                now=observed_at,
            )
            checkpoint_reason, checkpoint_status, checkpoint_available = self._checkpoint(
                before=before,
                result=result,
                propositions=propositions,
                created_at=observed_at,
                checkpoint_available=checkpoint_available,
            )
            self._states[session_id] = result.state

            current_snapshot = self._snapshots.get(session_id)
            if current_snapshot is not None and current_snapshot.character_id == character_id:
                self._snapshots[session_id] = current_snapshot.model_copy(
                    update={
                        "state": result.state,
                        "cognition": cognition,
                        "activity_summary": _activity_summary(result.state),
                        "processed_proposition_ids": result.processed_proposition_ids,
                        "ignored_proposition_ids": result.ignored_proposition_ids,
                        "checkpoint_status": (
                            checkpoint_status if checkpoint_available else "unavailable"
                        ),
                        "checkpoint_reason": checkpoint_reason,
                    }
                )

            return CompanionActivityUserTurnUpdate(
                session_id=session_id,
                character_id=character_id,
                message_id=message_id,
                state=result.state,
                cognition=cognition,
                processed_proposition_ids=result.processed_proposition_ids,
                ignored_proposition_ids=result.ignored_proposition_ids,
                recovered_from_checkpoint=recovered,
                checkpoint_status=(
                    checkpoint_status if checkpoint_available else "unavailable"
                ),
                checkpoint_reason=checkpoint_reason,
            )

    def snapshot(self, session_id: str) -> DesktopCompanionActivitySnapshot | None:
        with self._lock:
            return self._snapshots.get(session_id)

    def clear(self, session_id: str, capture_generation: str | None = None) -> bool:
        """Clear only the requested/current capture generation.

        Durable checkpoints intentionally remain available so an explicit objective,
        strategy, or open loop can survive a later share with a new capture generation.
        """

        with self._lock:
            current = self._states.get(session_id)
            if (
                capture_generation is not None
                and current is not None
                and current.generation != capture_generation
            ):
                return False
            self._states.pop(session_id, None)
            self._snapshots.pop(session_id, None)
            return True

    def _checkpoint(
        self,
        *,
        before: CompanionActivityState,
        result: ActivityRuntimeResult,
        propositions: tuple[EvidenceProposition, ...],
        created_at: datetime,
        checkpoint_available: bool,
    ) -> tuple[ActivityCheckpointReason | None, CheckpointRuntimeStatus, bool]:
        checkpoint_reason: ActivityCheckpointReason | None = None
        checkpoint_status: CheckpointRuntimeStatus = "not_needed"
        decision = self._checkpoint_policy.decide(
            before=before,
            result=result,
            propositions=propositions,
        )
        if decision.should_persist and decision.reason is not None:
            checkpoint_reason = decision.reason
            checkpoint = build_activity_checkpoint(
                state=result.state,
                reason=decision.reason,
                created_at=created_at,
                source_proposition_ids=result.processed_proposition_ids,
            )
            try:
                self._checkpoint_store.save(checkpoint)
                checkpoint_status = "persisted"
            except Exception:  # noqa: BLE001 - durability failure must not stop live state
                checkpoint_available = False
                checkpoint_status = "unavailable"
        return checkpoint_reason, checkpoint_status, checkpoint_available

    def _state_for(
        self,
        observation: DesktopObservation,
    ) -> tuple[CompanionActivityState, bool, bool]:
        cached = self._states.get(observation.session_id)
        if cached is not None and cached.character_id == observation.character_id:
            if cached.generation == observation.capture_generation:
                return cached, False, True
            if cached.generation is None:
                return (
                    cached.model_copy(
                        update={
                            "generation": observation.capture_generation,
                            "pending_transitions": (),
                        }
                    ),
                    False,
                    True,
                )

        activity_id = _activity_id(observation.session_id, observation.capture_generation)
        checkpoint_available = True
        checkpoint = None
        try:
            checkpoint = self._checkpoint_store.latest(
                observation.session_id,
                activity_id=activity_id,
            )
            if checkpoint is None:
                checkpoint = self._checkpoint_store.latest(observation.session_id)
        except Exception:  # noqa: BLE001 - recovery durability may be unavailable transiently
            checkpoint_available = False
        if (
            checkpoint is not None
            and checkpoint.reason != "activity_ended"
            and checkpoint.character_id == observation.character_id
        ):
            return (
                checkpoint.state.model_copy(
                    update={
                        "pending_transitions": (),
                        "generation": observation.capture_generation,
                    }
                ),
                True,
                checkpoint_available,
            )
        return (
            empty_activity_state(
                activity_id=activity_id,
                session_id=observation.session_id,
                character_id=observation.character_id,
                generation=observation.capture_generation,
                started_at=observation.observed_at,
            ),
            False,
            checkpoint_available,
        )

    def _state_for_user_turn(
        self,
        *,
        session_id: str,
        character_id: str | None,
        observed_at: datetime,
    ) -> tuple[CompanionActivityState, bool, bool]:
        cached = self._states.get(session_id)
        if cached is not None and cached.character_id == character_id:
            return cached, False, True

        checkpoint_available = True
        checkpoint = None
        try:
            checkpoint = self._checkpoint_store.latest(session_id)
        except Exception:  # noqa: BLE001 - activity state must not break ordinary Chat
            checkpoint_available = False
        if (
            checkpoint is not None
            and checkpoint.reason != "activity_ended"
            and checkpoint.character_id == character_id
        ):
            return (
                checkpoint.state.model_copy(update={"pending_transitions": ()}),
                True,
                checkpoint_available,
            )
        return (
            empty_activity_state(
                activity_id=_user_activity_id(session_id, character_id),
                session_id=session_id,
                character_id=character_id,
                generation=None,
                started_at=observed_at,
            ),
            False,
            checkpoint_available,
        )


def _activity_id(session_id: str, generation: str) -> str:
    material = f"{session_id}\x1f{generation}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"desktop-activity:{digest}"


def _user_activity_id(session_id: str, character_id: str | None) -> str:
    material = f"{session_id}\x1f{character_id or 'system'}\x1fuser-activity"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"session-activity:{digest}"


def _evidence_from_observation(
    observation: DesktopObservation,
    before: CompanionActivityState,
) -> tuple[EvidenceProposition, ...]:
    propositions: list[EvidenceProposition] = []
    activity_type = _activity_type(observation)
    if activity_type is not None and observation.activity.confidence >= 0.55:
        propositions.append(
            EvidenceProposition(
                proposition_id=f"{observation.observation_id}:activity-type",
                subject=before.activity_id,
                predicate="activity_type",
                value=activity_type,
                source_kind="external",
                trust_level="external_untrusted",
                confidence=observation.activity.confidence,
                sensitivity="sensitive",
                observed_at=observation.observed_at,
                valid_from=observation.observed_at,
                valid_until=observation.expires_at,
                generation=observation.capture_generation,
                schema_version="desktop-companion-activity-evidence@1",
            )
        )

    known_events = {item.event_id for item in before.recent_meaningful_events}
    for change in observation.visible_changes:
        proposition = _event_proposition(observation, before, change)
        event_id = str(proposition.value.get("event_id", ""))
        if event_id in known_events:
            continue
        known_events.add(event_id)
        propositions.append(proposition)
    return tuple(propositions)


def _event_proposition(
    observation: DesktopObservation,
    before: CompanionActivityState,
    change: DesktopObservedChange,
) -> EvidenceProposition:
    fingerprint = change.fingerprint or observation_fingerprint(
        change.event,
        prefix="change",
    )
    return EvidenceProposition(
        proposition_id=f"{observation.observation_id}:{fingerprint}"[:240],
        subject=before.activity_id,
        predicate="meaningful_event",
        value={
            "event_id": fingerprint,
            "kind": "activity",
            "description": change.event,
            "importance": max(observation.importance, change.confidence),
        },
        source_kind="external",
        trust_level="external_untrusted",
        confidence=change.confidence,
        sensitivity="sensitive",
        observed_at=observation.observed_at,
        valid_from=observation.observed_at,
        valid_until=observation.expires_at,
        generation=observation.capture_generation,
        schema_version="desktop-companion-activity-evidence@1",
    )


def _activity_type(observation: DesktopObservation) -> str | None:
    if observation.behavior.likely_media or observation.activity.hypothesis == "likely_media":
        return "watch"
    if observation.behavior.likely_typing or observation.activity.hypothesis == "likely_typing":
        return "work"
    if observation.behavior.current_pattern in {"browsing", "rapid_switching", "exploring"}:
        return "browse"
    if observation.behavior.current_pattern == "watching":
        return "watch"
    if observation.behavior.current_pattern == "typing":
        return "work"
    if observation.activity.hypothesis == "likely_navigation":
        return "browse"
    return None


def _observation_contains_prompt_injection(observation: DesktopObservation) -> bool:
    values = list(observation.visible_text)
    values.append(observation.current_scene.value)
    values.extend(item.event for item in observation.visible_changes)
    values.extend(item.event for item in observation.possible_events)
    values.extend(observation.uncertainties)
    if observation.plain_text_fallback:
        values.append(observation.plain_text_fallback)
    return screen_prompt_injection_observed(values)


def _activity_summary(state: CompanionActivityState) -> str:
    parts: list[str] = []
    labels = (
        ("activity_type", "Activity"),
        ("application_or_game", "Application/game"),
        ("current_objective", "Objective"),
        ("current_subtask", "Subtask"),
        ("current_phase", "Phase"),
        ("attempt_count", "Attempt"),
        ("strategy", "Strategy"),
    )
    for field_name, label in labels:
        field = state.field(field_name)
        if field is not None and field.value not in (None, ""):
            parts.append(f"{label}: {str(field.value)[:240]}")
    if state.blockers:
        parts.append(f"Blocker: {state.blockers[-1][:240]}")
    open_loops = [item for item in state.open_loops if item.status == "open"]
    if open_loops:
        parts.append(f"Open loop: {open_loops[-1].description[:240]}")
    if state.recent_meaningful_events:
        parts.append(
            f"Recent event: {state.recent_meaningful_events[-1].description[:240]}"
        )
    return " | ".join(parts)[:1800]


_default_activity_bridge: DesktopCompanionActivityBridge | None = None
_default_activity_bridge_lock = threading.Lock()


def default_desktop_companion_activity_bridge() -> DesktopCompanionActivityBridge:
    global _default_activity_bridge
    if _default_activity_bridge is None:
        with _default_activity_bridge_lock:
            if _default_activity_bridge is None:
                _default_activity_bridge = DesktopCompanionActivityBridge()
    return _default_activity_bridge


__all__ = [
    "CompanionActivityUserTurnUpdate",
    "DesktopCompanionActivityBridge",
    "DesktopCompanionActivitySnapshot",
    "default_desktop_companion_activity_bridge",
]
