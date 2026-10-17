"""Deterministic item-system scenario runner for autoplay and regressions.

This module composes the route-free item dispatcher and objective helpers into a
small scenario surface. It deliberately avoids route schemas and presentation
state: every executable step is an explicit engine-owned payload, and objectives
that still require loadout/route wiring are reported as blocked instead of being
silently guessed.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.item_objectives import build_item_objectives
from app.apps.rpg.session.item_session_actions import apply_item_session_action, available_item_session_actions
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

ITEM_SCENARIOS_SOURCE = "engine_item_scenarios_v1"
DISPATCHER_ACTIONS = {"buy", "sell", "market", "pickup", "collect", "take", "effect", "use_effect", "activate", "combat", "attack", "item_combat", "recipe_discovery", "discover_recipes", "recipes", "report", "item_report"}
LOADOUT_ONLY_ACTIONS = {"craft", "use", "equip", "salvage", "drop", "modify"}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _prepend_trace(state: dict[str, Any], key: str, trace: dict[str, Any]) -> dict[str, Any]:
    enriched = deepcopy(_safe_dict(trace))
    enriched["event"] = enriched.get("event") or "item_scenario_step"
    enriched["mechanics_source"] = ITEM_SCENARIOS_SOURCE
    enriched["turn"] = int(state.get("current_turn") or state.get("turn_count") or 0)
    enriched["timestamp"] = _utc_now()
    mechanics = _mechanics(state)
    traces = _safe_list(mechanics.get(key))
    mechanics[key] = [enriched, *traces][:50]
    item_traces = _safe_list(mechanics.get("item_traces"))
    mechanics["item_traces"] = [enriched, *item_traces][:50]
    return enriched


def _action_kind(action: dict[str, Any]) -> str:
    return _norm(_safe_dict(action).get("action") or _safe_dict(action).get("kind"))


def _step_from_objective(objective: dict[str, Any]) -> dict[str, Any]:
    action = deepcopy(_safe_dict(objective.get("action")))
    kind = _action_kind(action)
    executable = kind in DISPATCHER_ACTIONS
    blocked_reason = None if executable else "requires_loadout_or_route" if kind in LOADOUT_ONLY_ACTIONS else "unsupported_action"
    return {
        "step_id": _text(objective.get("objective_id"), f"objective:{kind or 'unknown'}"),
        "label": _text(objective.get("label"), kind or "item objective"),
        "category": _text(objective.get("category"), "item"),
        "priority": int(objective.get("priority") or 0),
        "action": action,
        "executable": executable,
        "blocked_reason": blocked_reason,
        "reason": objective.get("reason"),
    }


def build_item_scenario_plan(
    state: dict[str, Any],
    *,
    station: str | None = None,
    genre: str = "classic_fantasy",
    limit: int = 8,
    include_status_steps: bool = True,
) -> dict[str, Any]:
    """Build a deterministic item-system scenario plan without mutating state."""

    current = _safe_dict(state)
    objectives = build_item_objectives(current, station=station, genre=genre, limit=max(1, int(limit or 1)))
    steps = [_step_from_objective(_safe_dict(objective)) for objective in _safe_list(objectives.get("objectives"))]

    availability = available_item_session_actions(current)
    if include_status_steps:
        if _safe_list(availability.get("pickups")):
            node_id = _safe_dict(_safe_list(availability.get("pickups"))[0]).get("node_id")
            steps.insert(
                0,
                {
                    "step_id": f"pickup:{node_id}",
                    "label": "Collect available scene item",
                    "category": "scene_item",
                    "priority": 95,
                    "action": {"action": "pickup", "node_id": node_id, "source": "item_scenario"},
                    "executable": True,
                    "blocked_reason": None,
                    "reason": "available_scene_item",
                },
            )
        if _safe_list(availability.get("effects")):
            effect = _safe_dict(_safe_list(availability.get("effects"))[0])
            steps.insert(
                0,
                {
                    "step_id": f"effect:{effect.get('item_id') or effect.get('name')}",
                    "label": "Apply available item effect",
                    "category": "item_effect",
                    "priority": 88,
                    "action": {"action": "effect", "item_id": effect.get("item_id"), "effect_id": effect.get("effect_id"), "source": "item_scenario"},
                    "executable": True,
                    "blocked_reason": None,
                    "reason": "available_item_effect",
                },
            )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for step in sorted(steps, key=lambda item: (-int(item.get("priority") or 0), str(item.get("step_id") or ""))):
        step_id = _text(step.get("step_id"))
        if not step_id or step_id in seen:
            continue
        seen.add(step_id)
        deduped.append(step)
        if len(deduped) >= max(1, int(limit or 1)):
            break

    executable_count = sum(1 for step in deduped if step.get("executable") is True)
    blocked_count = len(deduped) - executable_count
    trace = {
        "event": "item_scenario_plan_built",
        "step_count": len(deduped),
        "executable_count": executable_count,
        "blocked_count": blocked_count,
        "mechanics_source": ITEM_SCENARIOS_SOURCE,
    }
    return {
        "ok": True,
        "steps": deduped,
        "availability": availability,
        "objectives": objectives,
        "summary": {"step_count": len(deduped), "executable_count": executable_count, "blocked_count": blocked_count},
        "trace": trace,
        "mechanics_source": ITEM_SCENARIOS_SOURCE,
    }


def run_item_scenario(
    state: dict[str, Any],
    *,
    steps: list[dict[str, Any]] | None = None,
    station: str | None = None,
    genre: str = "classic_fantasy",
    limit: int = 8,
    source: str = "item_scenario",
) -> dict[str, Any]:
    """Run executable item scenario steps against mutable session state."""

    current = _safe_dict(state)
    plan = build_item_scenario_plan(current, station=station, genre=genre, limit=limit) if steps is None else {"steps": deepcopy(steps)}
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for raw_step in _safe_list(plan.get("steps")):
        step = _safe_dict(raw_step)
        action = deepcopy(_safe_dict(step.get("action")))
        kind = _action_kind(action)
        if step.get("executable") is False or kind not in DISPATCHER_ACTIONS:
            skipped.append({"step_id": step.get("step_id"), "action": kind, "reason": step.get("blocked_reason") or "not_dispatcher_executable"})
            continue
        action.setdefault("source", source)
        result = apply_item_session_action(current, action)
        results.append({"step_id": step.get("step_id"), "action": kind, "ok": bool(_safe_dict(result).get("ok")), "result": result})

    ok_count = sum(1 for result in results if result.get("ok"))
    fail_count = len(results) - ok_count
    trace = _prepend_trace(
        current,
        "item_scenario_traces",
        {
            "event": "item_scenario_run",
            "attempted_count": len(results),
            "ok_count": ok_count,
            "failed_count": fail_count,
            "skipped_count": len(skipped),
            "source": source,
        },
    )
    return {
        "ok": fail_count == 0,
        "plan": plan,
        "results": results,
        "skipped": skipped,
        "summary": {"attempted_count": len(results), "ok_count": ok_count, "failed_count": fail_count, "skipped_count": len(skipped)},
        "mechanics_trace": trace,
        "mechanics_source": ITEM_SCENARIOS_SOURCE,
    }
