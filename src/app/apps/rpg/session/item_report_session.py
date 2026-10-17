"""Session bridge for deterministic RPG item report sections.

This module keeps item coverage/report assembly route-free while adding session
context and mechanics traces to the pure item report section helper.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.item_report_sections import build_item_report_section
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

ITEM_REPORT_SESSION_SOURCE = "engine_item_report_session_v1"


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _turn(state: dict[str, Any]) -> int:
    return int(state.get("current_turn") or state.get("turn_count") or 0)


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _prepend_trace(mechanics: dict[str, Any], key: str, trace: dict[str, Any]) -> None:
    traces = _safe_list(mechanics.get(key))
    mechanics[key] = [trace, *traces][:50]


def _enrich_trace(state: dict[str, Any], trace: dict[str, Any], *, source: str) -> dict[str, Any]:
    enriched = deepcopy(trace)
    enriched["session_event"] = "item_report_session_recorded"
    enriched["session_source"] = _text(source, "report")
    enriched["turn"] = _turn(state)
    enriched["timestamp"] = _utc_now()
    enriched["mechanics_source"] = ITEM_REPORT_SESSION_SOURCE
    return enriched


def build_item_report_for_session(
    state: dict[str, Any],
    *,
    station: str | None = None,
    genre: str = "classic_fantasy",
    source: str = "report",
) -> dict[str, Any]:
    """Build a session-context item report section without mutating state."""

    state = _safe_dict(state)
    section = deepcopy(build_item_report_section(state, station=station, genre=genre))
    trace = _enrich_trace(state, _safe_dict(section.get("trace")), source=source)
    section["trace"] = trace
    section["mechanics_source"] = ITEM_REPORT_SESSION_SOURCE
    return {
        "ok": True,
        "title": section.get("title"),
        "summary": deepcopy(_safe_dict(section.get("summary"))),
        "coverage": deepcopy(_safe_dict(section.get("coverage"))),
        "section": section,
        "detail": _detail(section),
        "mechanics_trace": trace,
    }


def record_item_report_for_session(
    state: dict[str, Any],
    *,
    station: str | None = None,
    genre: str = "classic_fantasy",
    source: str = "report",
) -> dict[str, Any]:
    """Record a session-context item report section and mirror its trace."""

    state = _safe_dict(state)
    result = build_item_report_for_session(state, station=station, genre=genre, source=source)
    section = deepcopy(_safe_dict(result.get("section")))
    trace = deepcopy(_safe_dict(result.get("mechanics_trace")))
    mechanics = _mechanics(state)
    sections = _safe_list(mechanics.get("item_report_sections"))
    mechanics["item_report_sections"] = [section, *sections][:20]
    _prepend_trace(mechanics, "item_report_session_traces", trace)
    _prepend_trace(mechanics, "item_traces", trace)
    return result


def _detail(section: dict[str, Any]) -> str:
    summary = _safe_dict(section.get("summary"))
    coverage = _safe_dict(section.get("coverage"))
    score = coverage.get("score", summary.get("coverage_score", 0))
    item_count = int(summary.get("item_count") or 0)
    enabled_actions = int(summary.get("enabled_action_count") or 0)
    return f"Item report recorded {item_count} item(s), {enabled_actions} enabled action(s), coverage {score}."
