"""Deterministic item-system turn hooks for long RPG sessions.

The helpers in this module are intentionally route-free. They compose the
existing item-system bridges into a small turn-boundary hook that can be called
by autoplay, save/checkpoint flows, or future session tick wiring without
changing public route schemas.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.item_diagnostics import build_item_diagnostics, record_item_diagnostics
from app.apps.rpg.session.item_objectives import build_item_objectives
from app.apps.rpg.session.item_report_session import build_item_report_for_session, record_item_report_for_session
from app.apps.rpg.session.item_state_maintenance import build_item_state_maintenance_plan, run_item_state_maintenance
from app.apps.rpg.session.recipe_discovery_session import apply_recipe_discovery_for_session
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MECHANICS_SOURCE = "engine_item_turn_hooks_v1"
TRACE_LIMIT = 20
ITEM_TRACE_LIMIT = 50


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _turn(state: dict[str, Any], current_turn: int | None = None) -> int:
    if current_turn is not None:
        return int(current_turn)
    return int(state.get("current_turn") or state.get("turn_count") or 0)


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _prepend_trace(mechanics: dict[str, Any], key: str, trace: dict[str, Any], *, limit: int) -> None:
    mechanics[key] = [deepcopy(trace), *_safe_list(mechanics.get(key))][: max(1, int(limit or 1))]


def _due(turn: int, interval: int, *, include_zero: bool = False) -> bool:
    interval = max(1, int(interval or 1))
    if turn == 0:
        return include_zero
    return turn % interval == 0


def _action_enabled(actions: list[dict[str, Any]], action: str) -> bool:
    return any(_safe_dict(entry).get("action") == action and _safe_dict(entry).get("enabled") is True for entry in actions)


def build_item_turn_hook_plan(
    state: dict[str, Any],
    *,
    current_turn: int | None = None,
    station: str | None = None,
    genre: str = "classic_fantasy",
    run_recipe_discovery: bool = True,
    diagnostics_interval: int = 10,
    maintenance_interval: int = 25,
    report_interval: int = 20,
    objective_limit: int = 5,
) -> dict[str, Any]:
    """Build a deterministic item turn-hook plan without mutating state."""

    source = deepcopy(_safe_dict(state))
    turn = _turn(source, current_turn)
    should_diagnose = _due(turn, diagnostics_interval, include_zero=True)
    should_maintain = _due(turn, maintenance_interval, include_zero=False)
    should_report = _due(turn, report_interval, include_zero=False)

    actions: list[dict[str, Any]] = [
        {"action": "recipe_discovery", "enabled": bool(run_recipe_discovery), "reason": "recipe_discovery_enabled"},
        {
            "action": "diagnostics",
            "enabled": should_diagnose,
            "reason": f"turn_mod_{max(1, int(diagnostics_interval or 1))}" if should_diagnose else "not_due",
        },
        {
            "action": "maintenance",
            "enabled": should_maintain,
            "reason": f"turn_mod_{max(1, int(maintenance_interval or 1))}" if should_maintain else "not_due",
        },
        {
            "action": "report",
            "enabled": should_report,
            "reason": f"turn_mod_{max(1, int(report_interval or 1))}" if should_report else "not_due",
        },
        {"action": "objectives", "enabled": True, "reason": "always_safe"},
    ]

    maintenance_plan = build_item_state_maintenance_plan(source, include_report=should_report)
    report_preview = build_item_report_for_session(source, station=station, genre=genre, source="turn_hook")
    diagnostics_preview = build_item_diagnostics(source, station=station, genre=genre, include_report=should_report)
    objectives_preview = build_item_objectives(source, station=station, genre=genre, limit=objective_limit)
    enabled_actions = [entry["action"] for entry in actions if entry.get("enabled")]

    return {
        "ok": True,
        "turn": turn,
        "actions": actions,
        "enabled_actions": enabled_actions,
        "summary": {
            "enabled_action_count": len(enabled_actions),
            "should_diagnose": _action_enabled(actions, "diagnostics"),
            "should_maintain": _action_enabled(actions, "maintenance"),
            "should_report": _action_enabled(actions, "report"),
            "objective_count": len(_safe_list(objectives_preview.get("objectives"))),
            "coverage_score": _safe_dict(report_preview.get("coverage")).get("score"),
            "audit_severity": _safe_dict(maintenance_plan.get("summary")).get("audit_severity"),
            "diagnostics_needs_attention": _safe_dict(diagnostics_preview.get("summary")).get("needs_attention"),
        },
        "maintenance_plan": maintenance_plan,
        "diagnostics_preview": diagnostics_preview,
        "report_preview": report_preview,
        "objectives_preview": objectives_preview,
        "mechanics_source": MECHANICS_SOURCE,
    }


def run_item_turn_hooks(
    state: dict[str, Any],
    *,
    current_turn: int | None = None,
    station: str | None = None,
    genre: str = "classic_fantasy",
    run_recipe_discovery: bool = True,
    diagnostics_interval: int = 10,
    maintenance_interval: int = 25,
    report_interval: int = 20,
    objective_limit: int = 5,
    record_trace: bool = True,
) -> dict[str, Any]:
    """Run the enabled item turn hooks against mutable session state."""

    mutable_state = state if isinstance(state, dict) else {}
    plan = build_item_turn_hook_plan(
        mutable_state,
        current_turn=current_turn,
        station=station,
        genre=genre,
        run_recipe_discovery=run_recipe_discovery,
        diagnostics_interval=diagnostics_interval,
        maintenance_interval=maintenance_interval,
        report_interval=report_interval,
        objective_limit=objective_limit,
    )
    results: dict[str, Any] = {}
    executed: list[str] = []

    if "recipe_discovery" in plan["enabled_actions"]:
        results["recipe_discovery"] = apply_recipe_discovery_for_session(
            mutable_state,
            source="turn_hook",
            record_empty=False,
        )
        executed.append("recipe_discovery")

    if "maintenance" in plan["enabled_actions"]:
        results["maintenance"] = run_item_state_maintenance(
            mutable_state,
            record_report=False,
            report_source="turn_hook_maintenance",
        )
        executed.append("maintenance")

    if "diagnostics" in plan["enabled_actions"]:
        results["diagnostics"] = record_item_diagnostics(
            mutable_state,
            station=station,
            genre=genre,
            objective_limit=objective_limit,
        )
        executed.append("diagnostics")

    if "report" in plan["enabled_actions"]:
        results["report"] = record_item_report_for_session(
            mutable_state,
            station=station,
            genre=genre,
            source="turn_hook",
        )
        executed.append("report")

    if "objectives" in plan["enabled_actions"]:
        results["objectives"] = build_item_objectives(mutable_state, station=station, genre=genre, limit=objective_limit)
        executed.append("objectives")

    trace = {
        "event": "item_turn_hooks_ran",
        "turn": plan["turn"],
        "executed_actions": list(executed),
        "enabled_actions": list(plan["enabled_actions"]),
        "objective_count": len(_safe_list(_safe_dict(results.get("objectives")).get("objectives"))),
        "mechanics_source": MECHANICS_SOURCE,
        "timestamp": _utc_now(),
    }
    if record_trace:
        mechanics = _mechanics(mutable_state)
        _prepend_trace(mechanics, "item_turn_hook_traces", trace, limit=TRACE_LIMIT)
        _prepend_trace(mechanics, "item_traces", trace, limit=ITEM_TRACE_LIMIT)

    return {
        "ok": True,
        "turn": plan["turn"],
        "plan": plan,
        "executed_actions": executed,
        "results": results,
        "mechanics_trace": trace,
        "recorded": bool(record_trace),
        "detail": f"Item turn hooks ran {len(executed)} action(s) for turn {plan['turn']}.",
        "mechanics_source": MECHANICS_SOURCE,
    }
