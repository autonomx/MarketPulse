from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Mapping
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

ARC_STATUS_ACTIVE = "active"
ARC_STATUS_ADVANCED = "advanced"
ARC_STATUS_READY_TO_RESOLVE = "ready_to_resolve"
ARC_STATUS_COMPLETED = "completed"
ARC_STATUS_FAILED = "failed"
ARC_STATUS_ABANDONED = "abandoned"
ARC_STATUS_STALLED = "stalled"

TERMINAL_ARC_STATUSES = {
    ARC_STATUS_COMPLETED,
    ARC_STATUS_FAILED,
    ARC_STATUS_ABANDONED,
}


@dataclass(frozen=True)
class ArcResolutionRule:
    id: str
    arc_id: str
    outcome: str
    requires_objectives: tuple[str, ...] = ()
    requires_flags: tuple[str, ...] = ()
    requires_items: tuple[str, ...] = ()
    requires_mechanics: tuple[str, ...] = ()
    blocked_by_flags: tuple[str, ...] = ()
    reward_xp: int = 0
    reward_items: tuple[Dict[str, Any], ...] = ()
    set_flags: tuple[str, ...] = ()
    clear_flags: tuple[str, ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class ArcFailureRule:
    id: str
    arc_id: str
    outcome: str
    fail_after_turns_without_progress: int = 0
    fail_if_flags: tuple[str, ...] = ()
    fail_if_missing_items: tuple[str, ...] = ()
    fail_if_location_left_without_resolution: bool = False
    summary: str = ""


@dataclass(frozen=True)
class ArcRuntimeState:
    arc_id: str
    title: str
    status: str = ARC_STATUS_ACTIVE
    current_stage: str = ""
    progress_count: int = 0
    last_progress_turn: int = 0
    started_turn: int = 0
    completed_turn: int = 0
    failed_turn: int = 0
    resolution_outcome: str = ""
    failure_outcome: str = ""
    completed_objectives: List[str] = field(default_factory=list)
    flags: Dict[str, bool] = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)
    source_hook_id: str = ""
    seeded_followup: bool = False


def _safe_str(value: Any) -> str:
    return str(value or "")


def _inventory_item_ids(state: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    inventory = state.get("inventory") or state.get("items") or []
    if isinstance(inventory, dict):
        ids.update(str(k) for k, v in inventory.items() if v)
    elif isinstance(inventory, list):
        for item in inventory:
            if isinstance(item, dict):
                item_id = item.get("id") or item.get("item_id") or item.get("name")
                if item_id:
                    ids.add(str(item_id))
            elif item:
                ids.add(str(item))
    return ids


def _state_flags(state: Mapping[str, Any]) -> set[str]:
    flags: set[str] = set()
    for key in ("flags", "world_flags", "scenario_flags"):
        raw = state.get(key)
        if isinstance(raw, dict):
            flags.update(str(k) for k, v in raw.items() if v)
        elif isinstance(raw, list):
            flags.update(str(v) for v in raw)
    return flags


def _completed_objectives(state: Mapping[str, Any]) -> set[str]:
    completed: set[str] = set()

    for key in ("completed_objectives", "completed_quests"):
        raw = state.get(key)
        if isinstance(raw, list):
            completed.update(str(v) for v in raw)

    quest_log = _safe_dict(state.get("quest_log"))
    for raw in _safe_list(quest_log.get("objectives")):
        objective = _safe_dict(raw)
        if objective.get("completed") or objective.get("status") == "completed":
            objective_id = objective.get("id") or objective.get("objective_id")
            if objective_id:
                completed.add(str(objective_id))

    return completed


def _covered_mechanics(state: Mapping[str, Any]) -> set[str]:
    covered: set[str] = set()

    mechanics = _safe_dict(state.get("mechanics_covered"))
    covered.update(str(k) for k, v in mechanics.items() if v)

    mechanics_summary = _safe_dict(state.get("mechanics_coverage_summary"))
    for name, data in _safe_dict(mechanics_summary.get("mechanics")).items():
        if int(_safe_dict(data).get("real_count") or _safe_dict(data).get("count") or 0) > 0:
            covered.add(str(name))

    return covered


def normalize_arc_state(raw: Mapping[str, Any]) -> ArcRuntimeState:
    raw = _safe_dict(raw)
    return ArcRuntimeState(
        arc_id=_safe_str(raw.get("arc_id") or raw.get("id")),
        title=_safe_str(raw.get("title") or raw.get("name") or raw.get("arc_id") or raw.get("id")),
        status=_safe_str(raw.get("status") or ARC_STATUS_ACTIVE),
        current_stage=_safe_str(raw.get("current_stage") or raw.get("stage")),
        progress_count=int(raw.get("progress_count") or 0),
        last_progress_turn=int(raw.get("last_progress_turn") or 0),
        started_turn=int(raw.get("started_turn") or 0),
        completed_turn=int(raw.get("completed_turn") or 0),
        failed_turn=int(raw.get("failed_turn") or 0),
        resolution_outcome=_safe_str(raw.get("resolution_outcome")),
        failure_outcome=_safe_str(raw.get("failure_outcome")),
        completed_objectives=[str(v) for v in _safe_list(raw.get("completed_objectives"))],
        flags={str(k): bool(v) for k, v in _safe_dict(raw.get("flags")).items()},
        history=[_safe_dict(v) for v in _safe_list(raw.get("history"))],
        source_hook_id=_safe_str(raw.get("source_hook_id")),
        seeded_followup=bool(
            raw.get("seeded_followup")
            or raw.get("source_hook_id")
            or raw.get("current_stage") == "seeded_followup"
        ),
    )


def serialize_arc_state(arc: ArcRuntimeState) -> Dict[str, Any]:
    return {
        "arc_id": arc.arc_id,
        "title": arc.title,
        "status": arc.status,
        "current_stage": arc.current_stage,
        "progress_count": arc.progress_count,
        "last_progress_turn": arc.last_progress_turn,
        "started_turn": arc.started_turn,
        "completed_turn": arc.completed_turn,
        "failed_turn": arc.failed_turn,
        "resolution_outcome": arc.resolution_outcome,
        "failure_outcome": arc.failure_outcome,
        "completed_objectives": list(arc.completed_objectives),
        "flags": dict(arc.flags),
        "history": list(arc.history),
        "source_hook_id": arc.source_hook_id,
        "seeded_followup": arc.seeded_followup,
    }


def _rule_requirements_met(rule: ArcResolutionRule, state: Mapping[str, Any]) -> bool:
    flags = _state_flags(state)
    items = _inventory_item_ids(state)
    completed = _completed_objectives(state)
    mechanics = _covered_mechanics(state)

    if any(obj not in completed for obj in rule.requires_objectives):
        return False
    if any(flag not in flags for flag in rule.requires_flags):
        return False
    if any(flag in flags for flag in rule.blocked_by_flags):
        return False
    if any(item not in items for item in rule.requires_items):
        return False
    if any(mechanic not in mechanics for mechanic in rule.requires_mechanics):
        return False

    return True


def _failure_rule_met(
    rule: ArcFailureRule,
    *,
    arc: ArcRuntimeState,
    state: Mapping[str, Any],
    turn_index: int,
) -> bool:
    flags = _state_flags(state)
    items = _inventory_item_ids(state)

    if any(flag in flags for flag in rule.fail_if_flags):
        return True

    if any(item not in items for item in rule.fail_if_missing_items):
        return True

    if rule.fail_after_turns_without_progress > 0:
        last_progress = arc.last_progress_turn or arc.started_turn or 0
        if last_progress and turn_index - last_progress >= rule.fail_after_turns_without_progress:
            return True

    return False


def apply_story_arc_lifecycle(
    *,
    arc_states: Mapping[str, Any],
    state: Mapping[str, Any],
    turn_index: int,
    resolution_rules: Iterable[ArcResolutionRule],
    failure_rules: Iterable[ArcFailureRule],
) -> Dict[str, Any]:
    arcs = {
        str(arc_id): normalize_arc_state(raw)
        for arc_id, raw in _safe_dict(arc_states).items()
    }

    events: List[Dict[str, Any]] = []
    state_delta: Dict[str, Any] = {
        "story_arcs": {},
        "flags": dict(_safe_dict(state.get("flags"))),
        "xp_delta": 0,
        "inventory_delta": {"items_added": []},
    }

    resolution_by_arc: Dict[str, List[ArcResolutionRule]] = {}
    for rule in resolution_rules:
        resolution_by_arc.setdefault(rule.arc_id, []).append(rule)

    failure_by_arc: Dict[str, List[ArcFailureRule]] = {}
    for arc_failure_rule in failure_rules:
        failure_by_arc.setdefault(arc_failure_rule.arc_id, []).append(arc_failure_rule)

    for arc_id, arc in arcs.items():
        if arc.status in TERMINAL_ARC_STATUSES:
            state_delta["story_arcs"][arc_id] = serialize_arc_state(arc)
            continue

        resolved = False

        for rule in resolution_by_arc.get(arc_id, []):
            if not _rule_requirements_met(rule, state):
                continue

            arc = replace(arc, status=ARC_STATUS_COMPLETED, completed_turn=int(turn_index), resolution_outcome=rule.outcome)
            arc.history.append(
                {
                    "turn": turn_index,
                    "type": "arc_completed",
                    "rule_id": rule.id,
                    "outcome": rule.outcome,
                    "summary": rule.summary,
                }
            )
            arcs[arc_id] = arc

            for flag in rule.set_flags:
                state_delta["flags"][flag] = True
            for flag in rule.clear_flags:
                state_delta["flags"][flag] = False

            if rule.reward_xp:
                state_delta["xp_delta"] = int(state_delta.get("xp_delta") or 0) + int(rule.reward_xp)

            for item in rule.reward_items:
                state_delta["inventory_delta"]["items_added"].append(dict(item))

            events.append(
                {
                    "type": "story_arc",
                    "subtype": "arc_completed",
                    "arc_id": arc_id,
                    "title": arc.title,
                    "outcome": rule.outcome,
                    "summary": rule.summary or f"{arc.title} is completed.",
                    "meaningful_progress": True,
                    "progress_category": "story_arc_resolution",
                }
            )
            resolved = True
            break

        if not resolved:
            for arc_failure_rule in failure_by_arc.get(arc_id, []):
                if not _failure_rule_met(arc_failure_rule, arc=arc, state=state, turn_index=turn_index):
                    continue

                arc = replace(arc, status=ARC_STATUS_FAILED, failed_turn=int(turn_index), failure_outcome=arc_failure_rule.outcome)
                arc.history.append(
                    {
                        "turn": turn_index,
                        "type": "arc_failed",
                        "rule_id": arc_failure_rule.id,
                        "outcome": arc_failure_rule.outcome,
                        "summary": arc_failure_rule.summary,
                    }
                )
                arcs[arc_id] = arc

                events.append(
                    {
                        "type": "story_arc",
                        "subtype": "arc_failed",
                        "arc_id": arc_id,
                        "title": arc.title,
                        "outcome": arc_failure_rule.outcome,
                        "summary": arc_failure_rule.summary or f"{arc.title} failed.",
                        "meaningful_progress": True,
                        "progress_category": "story_arc_resolution",
                    }
                )
                break

        state_delta["story_arcs"][arc_id] = serialize_arc_state(arc)

    if not state_delta["inventory_delta"]["items_added"]:
        state_delta.pop("inventory_delta", None)
    if not state_delta.get("xp_delta"):
        state_delta.pop("xp_delta", None)

    return {
        "ok": True,
        "story_arc_state_delta": state_delta,
        "story_arc_events": events,
        "resolved_count": sum(1 for event in events if event.get("subtype") == "arc_completed"),
        "failed_count": sum(1 for event in events if event.get("subtype") == "arc_failed"),
    }