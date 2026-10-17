"""Deterministic reducer from evidence propositions to revisable activity state."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from .authority import (
    ActivityAuthoritySource,
    ActivityFieldPolicy,
    activity_field_policy,
    authority_source_for,
)
from .contracts import EvidenceProposition
from .state import (
    ActivityField,
    ActivityReductionResult,
    ActivityStateChange,
    ActivityTransitionCandidate,
    CompanionActivityState,
)

_PENDING_MAX_AGE_SECONDS = 120.0


class ActivityReducer:
    """Apply field authority, hysteresis and staleness without producer-side mutation."""

    def reduce(
        self,
        state: CompanionActivityState,
        propositions: tuple[EvidenceProposition, ...],
        *,
        now: datetime,
    ) -> ActivityReductionResult:
        fields = dict(state.fields)
        changes: list[ActivityStateChange] = []
        ignored: list[str] = []
        pending = {
            (item.field_name, _value_key(item.value)): item
            for item in state.pending_transitions
            if (now - item.last_seen_at).total_seconds() <= _PENDING_MAX_AGE_SECONDS
        }
        self._expire_stale_fields(fields, changes, now=now)

        repeated = Counter(
            (item.predicate, _value_key(item.value))
            for item in propositions
            if item.source_kind == "external"
        )
        grouped: dict[str, list[tuple[EvidenceProposition, ActivityAuthoritySource]]] = {}
        for proposition in propositions:
            policy = activity_field_policy(proposition.predicate)
            if policy is None or not self._valid_now(proposition, now):
                ignored.append(proposition.proposition_id)
                continue
            source = authority_source_for(
                proposition,
                repeated_perception=(
                    proposition.source_kind == "external"
                    and repeated[(proposition.predicate, _value_key(proposition.value))] > 1
                ),
            )
            if not policy.source_allowed(source):
                ignored.append(proposition.proposition_id)
                continue
            grouped.setdefault(proposition.predicate, []).append((proposition, source))

        for field_name, candidates in grouped.items():
            policy = activity_field_policy(field_name)
            assert policy is not None
            selected, source = self._select_candidate(policy, candidates)
            current = fields.get(field_name)
            proposition_ids = tuple(
                sorted(
                    item.proposition_id
                    for item, item_source in candidates
                    if item_source == source
                    and _value_key(item.value) == _value_key(selected.value)
                )
            )

            if current is not None and _value_key(current.value) == _value_key(selected.value):
                fields[field_name] = ActivityField(
                    value=selected.value,
                    authority_source=self._stronger_source(
                        policy,
                        current.authority_source,
                        source,
                    ),
                    confidence=max(current.confidence, selected.confidence),
                    proposition_ids=tuple(
                        sorted(set(current.proposition_ids) | set(proposition_ids))
                    ),
                    stable_since=current.stable_since,
                    updated_at=now,
                    revision=current.revision,
                    last_transition_reason=current.last_transition_reason,
                )
                self._clear_pending_field(pending, field_name)
                continue

            effective_source = source
            effective_confidence = selected.confidence
            effective_ids = proposition_ids
            if policy.transition_rule == "hysteresis" and not self._bypasses_hysteresis(
                policy,
                source,
            ):
                transition = self._accumulate_transition(
                    pending,
                    field_name=field_name,
                    value=selected.value,
                    source=source,
                    confidence=selected.confidence,
                    proposition_ids=proposition_ids,
                    now=now,
                )
                effective_source = transition.authority_source
                effective_confidence = transition.confidence
                effective_ids = transition.proposition_ids
                if transition.confirmation_count < policy.confirmation_requirement:
                    continue

            if current is not None and not self._may_replace(
                policy,
                current,
                value=selected.value,
                confidence=effective_confidence,
                source=effective_source,
            ):
                ignored.extend(effective_ids)
                self._clear_pending_field(pending, field_name)
                continue

            reason = self._transition_reason(policy, current, effective_source)
            fields[field_name] = ActivityField(
                value=selected.value,
                authority_source=effective_source,
                confidence=effective_confidence,
                proposition_ids=effective_ids,
                stable_since=now,
                updated_at=now,
                revision=(current.revision + 1) if current else 1,
                last_transition_reason=reason,
            )
            self._clear_pending_field(pending, field_name)
            changes.append(
                ActivityStateChange(
                    field_name=field_name,
                    previous_value=current.value if current else None,
                    new_value=selected.value,
                    authority_source=effective_source,
                    proposition_ids=effective_ids,
                    reason=reason,
                    changed_at=now,
                )
            )

        pending_values = tuple(
            sorted(
                pending.values(),
                key=lambda item: (item.field_name, item.first_seen_at, _value_key(item.value)),
            )
        )
        generation = next(
            (item.generation for item in reversed(propositions) if item.generation),
            state.generation,
        )
        state_changed = bool(changes) or fields != state.fields or pending_values != state.pending_transitions
        if not state_changed and generation == state.generation:
            return ActivityReductionResult(
                state=state,
                ignored_proposition_ids=tuple(dict.fromkeys(ignored)),
            )
        next_state = state.model_copy(
            update={
                "fields": fields,
                "pending_transitions": pending_values,
                "revision": state.revision + (1 if changes else 0),
                "generation": generation,
                "last_meaningful_change_at": (
                    now if changes else state.last_meaningful_change_at
                ),
            }
        )
        return ActivityReductionResult(
            state=next_state,
            changes=tuple(changes),
            ignored_proposition_ids=tuple(dict.fromkeys(ignored)),
        )

    @staticmethod
    def _valid_now(proposition: EvidenceProposition, now: datetime) -> bool:
        if proposition.valid_from and now < proposition.valid_from:
            return False
        return not proposition.valid_until or now <= proposition.valid_until

    @staticmethod
    def _select_candidate(
        policy: ActivityFieldPolicy,
        candidates: list[tuple[EvidenceProposition, ActivityAuthoritySource]],
    ) -> tuple[EvidenceProposition, ActivityAuthoritySource]:
        def key(item: tuple[EvidenceProposition, ActivityAuthoritySource]) -> tuple[Any, ...]:
            proposition, source = item
            value_component = (
                _numeric_value(proposition.value)
                if policy.conflict_policy == "highest_count"
                else 0.0
            )
            return (
                -policy.authority_rank(source),
                value_component,
                proposition.confidence,
                proposition.observed_at.timestamp(),
                proposition.proposition_id,
            )

        return max(candidates, key=key)

    @staticmethod
    def _bypasses_hysteresis(
        policy: ActivityFieldPolicy,
        source: ActivityAuthoritySource,
    ) -> bool:
        if policy.family == "semantic_intent" and source == "user_explicit":
            return True
        return source in {
            "runtime_state",
            "trusted_process_integration",
            "deterministic_telemetry",
        }

    @staticmethod
    def _accumulate_transition(
        pending: dict[tuple[str, str], ActivityTransitionCandidate],
        *,
        field_name: str,
        value: Any,
        source: ActivityAuthoritySource,
        confidence: float,
        proposition_ids: tuple[str, ...],
        now: datetime,
    ) -> ActivityTransitionCandidate:
        key = (field_name, _value_key(value))
        existing = pending.get(key)
        ids = set(proposition_ids)
        if existing is not None:
            ids.update(existing.proposition_ids)
        effective_source = source
        if source == "single_perception" and len(ids) >= 2:
            effective_source = "repeated_perception"
        transition = ActivityTransitionCandidate(
            field_name=field_name,
            value=value,
            authority_source=effective_source,
            confidence=max(confidence, existing.confidence if existing else 0.0),
            proposition_ids=tuple(sorted(ids)),
            first_seen_at=existing.first_seen_at if existing else now,
            last_seen_at=now,
            confirmation_count=max(1, len(ids)),
        )
        pending[key] = transition
        return transition

    @staticmethod
    def _clear_pending_field(
        pending: dict[tuple[str, str], ActivityTransitionCandidate],
        field_name: str,
    ) -> None:
        for key in tuple(pending):
            if key[0] == field_name:
                pending.pop(key, None)

    @staticmethod
    def _may_replace(
        policy: ActivityFieldPolicy,
        current: ActivityField,
        *,
        value: Any,
        confidence: float,
        source: ActivityAuthoritySource,
    ) -> bool:
        if _value_key(current.value) == _value_key(value):
            return True
        new_rank = policy.authority_rank(source)
        current_rank = policy.authority_rank(current.authority_source)
        if new_rank < current_rank:
            return True
        if new_rank > current_rank:
            return False
        if policy.conflict_policy == "highest_count":
            return _numeric_value(value) >= _numeric_value(current.value)
        if policy.conflict_policy == "newest_within_authority":
            return True
        return confidence >= current.confidence

    @staticmethod
    def _stronger_source(
        policy: ActivityFieldPolicy,
        first: ActivityAuthoritySource,
        second: ActivityAuthoritySource,
    ) -> ActivityAuthoritySource:
        return first if policy.authority_rank(first) <= policy.authority_rank(second) else second

    @staticmethod
    def _transition_reason(
        policy: ActivityFieldPolicy,
        current: ActivityField | None,
        source: ActivityAuthoritySource,
    ) -> str:
        if current is None:
            return f"field_initialized:{source}"
        if policy.authority_rank(source) < policy.authority_rank(current.authority_source):
            return f"higher_field_authority:{source}"
        return f"confirmed_field_revision:{source}"

    @staticmethod
    def _expire_stale_fields(
        fields: dict[str, ActivityField],
        changes: list[ActivityStateChange],
        *,
        now: datetime,
    ) -> None:
        for field_name, field in tuple(fields.items()):
            policy = activity_field_policy(field_name)
            if (
                policy is None
                or policy.staleness_policy != "short_lived"
                or policy.stale_after_seconds is None
            ):
                continue
            if (now - field.updated_at).total_seconds() <= policy.stale_after_seconds:
                continue
            fields.pop(field_name, None)
            changes.append(
                ActivityStateChange(
                    field_name=field_name,
                    previous_value=field.value,
                    new_value=None,
                    authority_source=field.authority_source,
                    proposition_ids=field.proposition_ids,
                    reason="field_stale_expired",
                    changed_at=now,
                )
            )


def _value_key(value: Any) -> str:
    return repr(value)


def _numeric_value(value: Any) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return float("-inf")


__all__ = ["ActivityReducer"]
