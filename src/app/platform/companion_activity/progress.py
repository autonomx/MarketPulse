"""Progress, event, strategy, and open-loop reduction for companion activity."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from .authority import ActivityAuthoritySource, authority_source_for
from .contracts import EvidenceProposition, FrozenContract
from .state import (
    ActivityMeaningfulEvent,
    ActivityOpenLoop,
    ActivityProgressMarker,
    ActivityStateChange,
    ActivityStrategyChange,
    CompanionActivityState,
)

PROGRESS_PREDICATES = frozenset(
    {
        "progress_marker",
        "meaningful_event",
        "blocker",
        "open_loop",
        "open_loop_status",
    }
)

_TRUSTED_OPEN_LOOP_SOURCES: frozenset[ActivityAuthoritySource] = frozenset(
    {
        "user_explicit",
        "runtime_state",
        "trusted_process_integration",
        "deterministic_telemetry",
    }
)


class ActivityProgressReductionResult(FrozenContract):
    state: CompanionActivityState
    processed_proposition_ids: tuple[str, ...] = ()
    ignored_proposition_ids: tuple[str, ...] = ()


class ActivityProgressReducer:
    """Apply bounded progress/open-loop effects without mutating semantic fields directly."""

    def reduce(
        self,
        state: CompanionActivityState,
        propositions: tuple[EvidenceProposition, ...],
        *,
        state_changes: tuple[ActivityStateChange, ...] = (),
        now: datetime,
    ) -> ActivityProgressReductionResult:
        markers = list(state.progress_markers)
        events = list(state.recent_meaningful_events)
        loops = list(state.open_loops)
        strategies = list(state.strategy_changes)
        blockers = list(state.blockers)
        processed: list[str] = []
        ignored: list[str] = []
        changed = False

        for change in state_changes:
            if change.field_name != "strategy":
                continue
            strategies.append(
                ActivityStrategyChange(
                    change_id=_stable_id(
                        "strategy",
                        state.activity_id,
                        change.proposition_ids,
                        change.changed_at,
                    ),
                    previous_strategy=change.previous_value,
                    new_strategy=change.new_value,
                    proposition_ids=change.proposition_ids,
                    changed_at=change.changed_at,
                )
            )
            changed = True

        for proposition in propositions:
            if proposition.predicate == "progress_marker":
                marker = _progress_marker(proposition, now)
                updated_markers = _upsert_by_id(markers, marker, "marker_id")
                changed = changed or updated_markers != markers
                markers = updated_markers
                processed.append(proposition.proposition_id)
            elif proposition.predicate == "meaningful_event":
                event = _meaningful_event(proposition, now)
                updated_events = _upsert_by_id(events, event, "event_id")
                changed = changed or updated_events != events
                events = updated_events
                processed.append(proposition.proposition_id)
            elif proposition.predicate == "blocker":
                blocker = _mapping_text(proposition.value, "description") or _text(
                    proposition.value
                )
                if not blocker:
                    ignored.append(proposition.proposition_id)
                    continue
                if blocker not in blockers:
                    blockers.append(blocker)
                    changed = True
                processed.append(proposition.proposition_id)
            elif proposition.predicate == "open_loop":
                authority = authority_source_for(proposition)
                if authority not in _TRUSTED_OPEN_LOOP_SOURCES:
                    ignored.append(proposition.proposition_id)
                    continue
                loop = _open_loop(
                    proposition,
                    authority,
                    now,
                    activity_id=state.activity_id,
                )
                existing = next((item for item in loops if item.loop_id == loop.loop_id), None)
                if existing is None:
                    loops.append(loop)
                    changed = True
                else:
                    updated = existing.model_copy(
                        update={
                            "description": loop.description or existing.description,
                            "confidence": max(existing.confidence, loop.confidence),
                            "importance": max(existing.importance, loop.importance),
                            "blocking": existing.blocking or loop.blocking,
                            "last_referenced_at": now,
                            "created_from": tuple(
                                dict.fromkeys((*existing.created_from, *loop.created_from))
                            ),
                        }
                    )
                    loops = [
                        updated if item.loop_id == updated.loop_id else item for item in loops
                    ]
                    changed = changed or updated != existing
                processed.append(proposition.proposition_id)
            elif proposition.predicate == "open_loop_status":
                loop_id = _mapping_text(proposition.value, "loop_id")
                status = _mapping_text(proposition.value, "status")
                if not loop_id or status not in {
                    "open",
                    "resolved",
                    "abandoned",
                    "superseded",
                }:
                    ignored.append(proposition.proposition_id)
                    continue
                existing_index = next(
                    (index for index, item in enumerate(loops) if item.loop_id == loop_id),
                    None,
                )
                if existing_index is None:
                    ignored.append(proposition.proposition_id)
                    continue
                authority = authority_source_for(proposition)
                if authority not in _TRUSTED_OPEN_LOOP_SOURCES:
                    ignored.append(proposition.proposition_id)
                    continue
                existing = loops[existing_index]
                resolution = (
                    tuple(
                        dict.fromkeys(
                            (*existing.resolution_evidence, proposition.proposition_id)
                        )
                    )
                    if status != "open"
                    else existing.resolution_evidence
                )
                updated = existing.model_copy(
                    update={
                        "status": status,
                        "last_referenced_at": now,
                        "resolution_evidence": resolution,
                    }
                )
                if updated != existing:
                    loops[existing_index] = updated
                    changed = True
                processed.append(proposition.proposition_id)

        next_state = state
        if changed:
            next_state = state.model_copy(
                update={
                    "progress_markers": tuple(markers[-32:]),
                    "recent_meaningful_events": tuple(events[-32:]),
                    "strategy_changes": tuple(strategies[-16:]),
                    "blockers": tuple(dict.fromkeys(blockers[-16:])),
                    "open_loops": tuple(loops[-32:]),
                    "last_meaningful_change_at": now,
                }
            )
        return ActivityProgressReductionResult(
            state=next_state,
            processed_proposition_ids=tuple(dict.fromkeys(processed)),
            ignored_proposition_ids=tuple(dict.fromkeys(ignored)),
        )


def _progress_marker(
    proposition: EvidenceProposition,
    now: datetime,
) -> ActivityProgressMarker:
    return ActivityProgressMarker(
        marker_id=_mapping_text(proposition.value, "marker_id")
        or _stable_id("progress", proposition.subject, (proposition.proposition_id,), now),
        description=_mapping_text(proposition.value, "description") or _text(proposition.value),
        proposition_ids=(proposition.proposition_id,),
        confidence=proposition.confidence,
        recorded_at=proposition.observed_at,
    )


def _meaningful_event(
    proposition: EvidenceProposition,
    now: datetime,
) -> ActivityMeaningfulEvent:
    return ActivityMeaningfulEvent(
        event_id=_mapping_text(proposition.value, "event_id")
        or _stable_id("event", proposition.subject, (proposition.proposition_id,), now),
        kind=_mapping_text(proposition.value, "kind") or "event",
        description=_mapping_text(proposition.value, "description") or _text(proposition.value),
        proposition_ids=(proposition.proposition_id,),
        confidence=proposition.confidence,
        occurred_at=proposition.observed_at,
    )


def _open_loop(
    proposition: EvidenceProposition,
    authority: ActivityAuthoritySource,
    now: datetime,
    *,
    activity_id: str,
) -> ActivityOpenLoop:
    loop_id = _mapping_text(proposition.value, "loop_id") or _stable_id(
        "loop",
        proposition.subject,
        (proposition.proposition_id,),
        now,
    )
    return ActivityOpenLoop(
        loop_id=loop_id,
        activity_id=activity_id,
        kind=_mapping_text(proposition.value, "kind") or "open_loop",
        description=_mapping_text(proposition.value, "description") or _text(proposition.value),
        created_from=(proposition.proposition_id,),
        authority_source=authority,
        confidence=proposition.confidence,
        importance=_mapping_float(proposition.value, "importance", 0.5),
        blocking=_mapping_bool(proposition.value, "blocking", False),
        opened_at=proposition.observed_at,
        last_referenced_at=now,
    )


def _stable_id(
    prefix: str,
    subject: str,
    proposition_ids: tuple[str, ...],
    observed_at: datetime,
) -> str:
    material = "|".join((prefix, subject, observed_at.isoformat(), *proposition_ids))
    return f"{prefix}:{hashlib.sha256(material.encode()).hexdigest()[:20]}"


def _upsert_by_id(items: list[Any], value: Any, field_name: str) -> list[Any]:
    identifier = getattr(value, field_name)
    existing = next((item for item in items if getattr(item, field_name) == identifier), None)
    if existing == value:
        return items
    result = [item for item in items if getattr(item, field_name) != identifier]
    result.append(value)
    return result


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()[:1000]
    if isinstance(value, dict):
        for key in ("description", "value", "text", "event"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()[:1000]
    return str(value)[:1000]


def _mapping_text(value: Any, key: str) -> str:
    if not isinstance(value, dict):
        return ""
    candidate = value.get(key)
    return candidate.strip()[:1000] if isinstance(candidate, str) else ""


def _mapping_float(value: Any, key: str, default: float) -> float:
    if not isinstance(value, dict):
        return default
    candidate = value.get(key, default)
    try:
        candidate = float(candidate)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, candidate))


def _mapping_bool(value: Any, key: str, default: bool) -> bool:
    if not isinstance(value, dict):
        return default
    candidate = value.get(key, default)
    return candidate if isinstance(candidate, bool) else default


__all__ = [
    "PROGRESS_PREDICATES",
    "ActivityProgressReducer",
    "ActivityProgressReductionResult",
]
