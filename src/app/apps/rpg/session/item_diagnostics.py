"""Deterministic item-system diagnostics for long-run RPG sessions.

This module composes existing item-system helpers into one compact status payload
for autoplay, debug panels, and handoff reports. It deliberately avoids route
schemas and gameplay mutation unless the caller explicitly records the diagnostic
trace.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.item_objectives import build_item_objectives
from app.apps.rpg.session.item_report_sections import build_item_report_section
from app.apps.rpg.session.item_scenarios import build_item_scenario_plan
from app.apps.rpg.session.item_state_maintenance import build_item_state_maintenance_plan
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MECHANICS_SOURCE = "engine_item_diagnostics_v1"
TRACE_LIMIT = 20
ITEM_TRACE_LIMIT = 50


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _record_trace(state: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    enriched = deepcopy(_safe_dict(trace))
    enriched["event"] = enriched.get("event") or "item_diagnostics_recorded"
    enriched["mechanics_source"] = MECHANICS_SOURCE
    enriched["turn"] = int(state.get("current_turn") or state.get("turn_count") or 0)
    enriched["timestamp"] = _utc_now()
    mechanics = _mechanics(state)
    mechanics["item_diagnostic_traces"] = [
        deepcopy(enriched),
        *_safe_list(mechanics.get("item_diagnostic_traces")),
    ][:TRACE_LIMIT]
    mechanics["item_traces"] = [deepcopy(enriched), *_safe_list(mechanics.get("item_traces"))][:ITEM_TRACE_LIMIT]
    return enriched


def _objective_summaries(objectives: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for objective in objectives:
        current = _safe_dict(objective)
        action = _safe_dict(current.get("action"))
        summaries.append(
            {
                "objective_id": _text(current.get("objective_id")),
                "category": _text(current.get("category"), "item"),
                "priority": int(current.get("priority") or 0),
                "action": _text(action.get("action") or action.get("kind")),
                "target": _text(action.get("item_name") or action.get("item_id") or action.get("recipe_id") or action.get("target_id")),
                "reason": current.get("reason"),
            }
        )
        if len(summaries) >= max(0, int(limit or 0)):
            break
    return summaries


def _summary(
    *,
    maintenance: dict[str, Any],
    report: dict[str, Any],
    scenario: dict[str, Any],
    objectives: dict[str, Any],
) -> dict[str, Any]:
    audit_summary = _safe_dict(maintenance.get("summary"))
    report_summary = _safe_dict(report.get("summary"))
    coverage = _safe_dict(report.get("coverage"))
    scenario_summary = _safe_dict(scenario.get("summary"))
    objective_items = _safe_list(objectives.get("objectives"))
    issue_count = int(audit_summary.get("audit_issue_count") or 0)
    warning_count = int(audit_summary.get("audit_warning_count") or 0)
    coverage_score = coverage.get("score", report_summary.get("coverage_score"))
    executable_count = int(scenario_summary.get("executable_count") or 0)
    blocked_count = int(scenario_summary.get("blocked_count") or 0)
    return {
        "ok": bool(maintenance.get("ok")) and issue_count == 0,
        "audit_severity": audit_summary.get("audit_severity"),
        "audit_issue_count": issue_count,
        "audit_warning_count": warning_count,
        "coverage_score": coverage_score,
        "coverage_missing_count": len(_safe_list(coverage.get("missing"))),
        "scenario_step_count": int(scenario_summary.get("step_count") or 0),
        "scenario_executable_count": executable_count,
        "scenario_blocked_count": blocked_count,
        "objective_count": len(objective_items),
        "needs_attention": issue_count > 0 or warning_count > 0 or blocked_count > executable_count,
    }


def build_item_diagnostics(
    state: dict[str, Any],
    *,
    station: str | None = None,
    genre: str = "classic_fantasy",
    scenario_limit: int = 8,
    objective_limit: int = 8,
    include_report: bool = True,
) -> dict[str, Any]:
    """Build a compact deterministic item-system diagnostic payload."""

    current = deepcopy(_safe_dict(state))
    maintenance = build_item_state_maintenance_plan(current, include_report=include_report)
    report = build_item_report_section(current, station=station, genre=genre)
    scenario = build_item_scenario_plan(current, station=station, genre=genre, limit=scenario_limit)
    objectives = build_item_objectives(current, station=station, genre=genre, limit=objective_limit)
    summary = _summary(
        maintenance=maintenance,
        report=report,
        scenario=scenario,
        objectives=objectives,
    )
    coverage = _safe_dict(report.get("coverage"))
    top_objectives = _objective_summaries(_safe_list(objectives.get("objectives")), limit=objective_limit)
    trace = {
        "event": "item_diagnostics_built",
        "summary": deepcopy(summary),
        "coverage_score": summary.get("coverage_score"),
        "scenario_executable_count": summary.get("scenario_executable_count"),
        "mechanics_source": MECHANICS_SOURCE,
    }
    return {
        "ok": bool(summary.get("ok")),
        "summary": summary,
        "maintenance": maintenance,
        "report": report,
        "scenario": scenario,
        "objectives": objectives,
        "top_objectives": top_objectives,
        "gaps": {
            "coverage_missing": _safe_list(coverage.get("missing")),
            "audit_issues": _safe_list(_safe_dict(maintenance.get("audit")).get("issues")),
            "audit_warnings": _safe_list(_safe_dict(maintenance.get("audit")).get("warnings")),
            "blocked_steps": [
                {"step_id": step.get("step_id"), "reason": step.get("blocked_reason")}
                for step in _safe_list(scenario.get("steps"))
                if _safe_dict(step).get("blocked_reason")
            ],
        },
        "trace": trace,
        "mechanics_source": MECHANICS_SOURCE,
    }


def record_item_diagnostics(
    state: dict[str, Any],
    *,
    station: str | None = None,
    genre: str = "classic_fantasy",
    scenario_limit: int = 8,
    objective_limit: int = 8,
) -> dict[str, Any]:
    """Build item diagnostics and record only the compact diagnostic trace."""

    mutable_state = state if isinstance(state, dict) else {}
    diagnostics = build_item_diagnostics(
        mutable_state,
        station=station,
        genre=genre,
        scenario_limit=scenario_limit,
        objective_limit=objective_limit,
    )
    trace = _record_trace(mutable_state, _safe_dict(diagnostics.get("trace")))
    diagnostics["mechanics_trace"] = trace
    return diagnostics
