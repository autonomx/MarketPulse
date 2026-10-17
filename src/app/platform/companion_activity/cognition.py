"""Companion cognition outputs state effects independently from user-facing delivery."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.conversation.memory_policy import Sensitivity, TrustLevel, strongest_sensitivity, weakest_trust

from .contracts import EvidenceProposition, FrozenContract
from .runtime import ActivityRuntimeResult
from .state import CompanionActivityState

DeliveryIntentKind = Literal[
    "IGNORE",
    "REACT",
    "ASK",
    "ADVISE",
    "CELEBRATE",
    "WARN",
    "RESUME",
]

_SILENT_CONTEXT_FIELDS = frozenset(
    {
        "activity_type",
        "application_or_game",
        "foreground_application",
        "voice_call_connected",
    }
)


class MemoryCandidate(FrozenContract):
    candidate_id: str = Field(min_length=1, max_length=240)
    kind: Literal["episode", "goal", "open_loop", "correction", "progress"]
    proposition_ids: tuple[str, ...] = Field(min_length=1)
    trust_level: TrustLevel
    sensitivity: Sensitivity
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=500)


class OpenLoopEffect(FrozenContract):
    loop_id: str = Field(min_length=1, max_length=240)
    status: Literal["open", "resolved", "abandoned", "superseded"]
    proposition_ids: tuple[str, ...] = ()


class StateEffects(FrozenContract):
    activity_change_fields: tuple[str, ...] = ()
    memory_candidates: tuple[MemoryCandidate, ...] = ()
    open_loop_updates: tuple[OpenLoopEffect, ...] = ()


class DeliveryIntent(FrozenContract):
    intent_id: str = Field(min_length=1, max_length=240)
    session_id: str = Field(min_length=1, max_length=200)
    kind: DeliveryIntentKind
    reason: str = Field(min_length=1, max_length=500)
    grounding_proposition_ids: tuple[str, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    salience: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime


class CognitionResult(FrozenContract):
    effects: StateEffects
    delivery_intent: DeliveryIntent


class CompanionCognition:
    """Interpret accepted evidence without conflating learning with speaking."""

    def evaluate(
        self,
        *,
        before: CompanionActivityState,
        activity_result: ActivityRuntimeResult,
        propositions: tuple[EvidenceProposition, ...],
        now: datetime,
    ) -> CognitionResult:
        admissible_ids = set(activity_result.admissible_proposition_ids)
        admissible = tuple(
            item for item in propositions if item.proposition_id in admissible_ids
        )
        effects = self._state_effects(before, activity_result, admissible)
        delivery = self._delivery_intent(
            before=before,
            after=activity_result.state,
            activity_result=activity_result,
            propositions=admissible,
            now=now,
        )
        return CognitionResult(effects=effects, delivery_intent=delivery)

    def _state_effects(
        self,
        before: CompanionActivityState,
        result: ActivityRuntimeResult,
        propositions: tuple[EvidenceProposition, ...],
    ) -> StateEffects:
        candidates: list[MemoryCandidate] = []
        proposition_index = {item.proposition_id: item for item in propositions}
        for proposition in propositions:
            candidate = _memory_candidate(proposition)
            if candidate is not None:
                candidates.append(candidate)

        before_loops = {item.loop_id: item for item in before.open_loops}
        loop_updates: list[OpenLoopEffect] = []
        for loop in result.state.open_loops:
            previous = before_loops.get(loop.loop_id)
            if previous is not None and previous.status == loop.status:
                continue
            loop_updates.append(
                OpenLoopEffect(
                    loop_id=loop.loop_id,
                    status=loop.status,
                    proposition_ids=(
                        loop.resolution_evidence
                        if loop.status != "open"
                        else loop.created_from
                    ),
                )
            )

        for change in result.changes:
            if not change.reason.startswith("higher_field_authority:user_explicit"):
                continue
            backing = [
                proposition_index[item]
                for item in change.proposition_ids
                if item in proposition_index
            ]
            if not backing:
                continue
            candidates.append(
                _candidate_from_evidence(
                    candidate_id=f"memory:correction:{change.field_name}:{backing[0].proposition_id}",
                    kind="correction",
                    evidence=tuple(backing),
                    confidence=max(item.confidence for item in backing),
                    importance=0.85,
                    reason=f"explicit user correction changed {change.field_name}",
                )
            )

        return StateEffects(
            activity_change_fields=tuple(change.field_name for change in result.changes),
            memory_candidates=tuple(_dedupe_candidates(candidates)),
            open_loop_updates=tuple(loop_updates),
        )

    def _delivery_intent(
        self,
        *,
        before: CompanionActivityState,
        after: CompanionActivityState,
        activity_result: ActivityRuntimeResult,
        propositions: tuple[EvidenceProposition, ...],
        now: datetime,
    ) -> DeliveryIntent:
        warning = _best_special_event(propositions, {"safety_warning", "destructive_risk"})
        if warning is not None:
            return _intent(
                after,
                "WARN",
                "high-consequence warning evidence",
                (warning,),
                now,
                salience=1.0,
            )

        success = _best_meaningful_event(propositions, {"success", "completion", "milestone"})
        if success is not None:
            return _intent(
                after,
                "CELEBRATE",
                "meaningful success or completion",
                (success,),
                now,
                salience=0.9,
            )

        before_loops = {item.loop_id: item for item in before.open_loops}
        resolved = next(
            (
                loop
                for loop in after.open_loops
                if loop.status == "resolved"
                and before_loops.get(loop.loop_id) is not None
                and before_loops[loop.loop_id].status == "open"
            ),
            None,
        )
        if resolved is not None:
            evidence = tuple(
                item
                for item in propositions
                if item.proposition_id in resolved.resolution_evidence
            )
            return _intent(
                after,
                "ASK",
                "an established open loop was resolved",
                evidence,
                now,
                salience=max(0.6, resolved.importance),
            )

        new_blocker = next(
            (item for item in propositions if item.predicate == "blocker" and item.confidence >= 0.7),
            None,
        )
        if new_blocker is not None:
            return _intent(
                after,
                "ADVISE",
                "a meaningful blocker became active",
                (new_blocker,),
                now,
                salience=0.7,
            )

        event = _best_meaningful_event(propositions, {"failure", "attempt", "activity"})
        if event is not None:
            return _intent(
                after,
                "REACT",
                "a meaningful activity event occurred",
                (event,),
                now,
                salience=0.65,
            )

        delivery_changes = tuple(
            change
            for change in activity_result.changes
            if change.field_name not in _SILENT_CONTEXT_FIELDS
        )
        user_correction_only = bool(delivery_changes) and all(
            change.reason.startswith("higher_field_authority:user_explicit")
            or change.reason.startswith("field_initialized:user_explicit")
            for change in delivery_changes
        )
        if delivery_changes and not user_correction_only:
            ids = tuple(
                item
                for item in propositions
                if item.proposition_id
                in {
                    proposition_id
                    for change in delivery_changes
                    for proposition_id in change.proposition_ids
                }
            )
            return _intent(
                after,
                "REACT",
                "meaningful activity state changed",
                ids,
                now,
                salience=0.55,
            )

        return DeliveryIntent(
            intent_id=f"intent:{after.activity_id}:{after.revision}:{int(now.timestamp() * 1000)}",
            session_id=after.session_id,
            kind="IGNORE",
            reason="state may update without a worthwhile user-facing interruption",
            grounding_proposition_ids=(),
            confidence=1.0,
            salience=0.0,
            created_at=now,
        )


def _memory_candidate(proposition: EvidenceProposition) -> MemoryCandidate | None:
    if proposition.confidence < 0.75:
        return None
    if proposition.predicate == "meaningful_event":
        return _candidate_from_evidence(
            candidate_id=f"memory:event:{proposition.proposition_id}",
            kind="episode",
            evidence=(proposition,),
            confidence=proposition.confidence,
            importance=0.8,
            reason="meaningful activity event",
        )
    if proposition.predicate == "progress_marker":
        return _candidate_from_evidence(
            candidate_id=f"memory:progress:{proposition.proposition_id}",
            kind="progress",
            evidence=(proposition,),
            confidence=proposition.confidence,
            importance=0.7,
            reason="activity progress checkpoint",
        )
    if proposition.predicate == "open_loop":
        return _candidate_from_evidence(
            candidate_id=f"memory:open-loop:{proposition.proposition_id}",
            kind="open_loop",
            evidence=(proposition,),
            confidence=proposition.confidence,
            importance=0.75,
            reason="new unresolved activity commitment",
        )
    return None


def _candidate_from_evidence(
    *,
    candidate_id: str,
    kind: Literal["episode", "goal", "open_loop", "correction", "progress"],
    evidence: tuple[EvidenceProposition, ...],
    confidence: float,
    importance: float,
    reason: str,
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        kind=kind,
        proposition_ids=tuple(item.proposition_id for item in evidence),
        trust_level=weakest_trust(item.trust_level for item in evidence),
        sensitivity=strongest_sensitivity(item.sensitivity for item in evidence),
        confidence=confidence,
        importance=importance,
        reason=reason,
    )


def _dedupe_candidates(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    return list({item.candidate_id: item for item in candidates}.values())


def _best_special_event(
    propositions: tuple[EvidenceProposition, ...],
    predicates: set[str],
) -> EvidenceProposition | None:
    values = [
        item
        for item in propositions
        if item.predicate in predicates and item.confidence >= 0.75
    ]
    return max(values, key=lambda item: item.confidence, default=None)


def _best_meaningful_event(
    propositions: tuple[EvidenceProposition, ...],
    kinds: set[str],
) -> EvidenceProposition | None:
    values = []
    for item in propositions:
        if item.predicate != "meaningful_event" or item.confidence < 0.65:
            continue
        if isinstance(item.value, dict):
            kind = str(item.value.get("kind") or "activity")
        else:
            kind = "activity"
        if kind in kinds:
            values.append(item)
    return max(values, key=lambda item: item.confidence, default=None)


def _intent(
    state: CompanionActivityState,
    kind: DeliveryIntentKind,
    reason: str,
    evidence: tuple[EvidenceProposition, ...],
    now: datetime,
    *,
    salience: float,
) -> DeliveryIntent:
    confidence = min((item.confidence for item in evidence), default=1.0)
    return DeliveryIntent(
        intent_id=f"intent:{state.activity_id}:{kind.lower()}:{int(now.timestamp() * 1000)}",
        session_id=state.session_id,
        kind=kind,
        reason=reason,
        grounding_proposition_ids=tuple(item.proposition_id for item in evidence),
        confidence=confidence,
        salience=salience,
        created_at=now,
    )


__all__ = [
    "CognitionResult",
    "CompanionCognition",
    "DeliveryIntent",
    "DeliveryIntentKind",
    "MemoryCandidate",
    "OpenLoopEffect",
    "StateEffects",
]
