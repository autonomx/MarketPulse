"""Deterministic item-state maintenance orchestration for long RPG sessions."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.item_report_session import record_item_report_for_session
from app.apps.rpg.session.item_state_audit import build_item_state_audit, record_item_state_audit
from app.apps.rpg.session.item_state_compaction import (
    DEFAULT_BUCKET_LIMIT,
    apply_item_state_compaction,
    build_item_state_compaction,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MECHANICS_SOURCE = "engine_item_state_maintenance_v1"
TRACE_LIMIT = 20
ITEM_TRACE_LIMIT = 50


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _needs_compaction(compaction_plan: dict[str, Any], *, threshold: int) -> bool:
    summary = _safe_dict(compaction_plan.get("summary"))
    if compaction_plan.get("changed") is True:
        return True
    if int(summary.get("total_before") or 0) > max(0, threshold):
        return True
    return False


def _summary(
    *,
    before_audit: dict[str, Any],
    compaction: dict[str, Any] | None,
    after_audit: dict[str, Any] | None,
    report: dict[str, Any] | None,
) -> dict[str, Any]:
    compaction_summary = _safe_dict((compaction or {}).get("summary"))
    report_summary = _safe_dict((report or {}).get("summary"))
    return {
        "audit_severity": before_audit.get("severity"),
        "audit_issue_count": len(_safe_list(before_audit.get("issues"))),
        "audit_warning_count": len(_safe_list(before_audit.get("warnings"))),
        "compacted": bool((compaction or {}).get("changed")),
        "compaction_dropped": int(compaction_summary.get("total_dropped") or 0),
        "after_audit_severity": (after_audit or before_audit).get("severity"),
        "report_recorded": report is not None,
        "report_score": _safe_dict((report or {}).get("coverage")).get("score", report_summary.get("coverage_score")),
    }


def build_item_state_maintenance_plan(
    state: dict[str, Any],
    *,
    bucket_limit: int = DEFAULT_BUCKET_LIMIT,
    compaction_threshold: int = DEFAULT_BUCKET_LIMIT,
    include_report: bool = False,
) -> dict[str, Any]:
    """Build a deterministic maintenance plan without mutating session state."""

    source = deepcopy(_safe_dict(state))
    audit = build_item_state_audit(source)
    compaction_plan = build_item_state_compaction(source, bucket_limit=bucket_limit)
    should_compact = _needs_compaction(compaction_plan, threshold=compaction_threshold)
    actions = ["audit"]
    if should_compact:
        actions.append("compact")
    if include_report:
        actions.append("report")
    return {
        "ok": audit.get("ok") is True and compaction_plan.get("ok") is True,
        "actions": actions,
        "audit": audit,
        "compaction_plan": compaction_plan,
        "should_compact": should_compact,
        "include_report": include_report,
        "summary": {
            "audit_severity": audit.get("severity"),
            "audit_issue_count": len(_safe_list(audit.get("issues"))),
            "audit_warning_count": len(_safe_list(audit.get("warnings"))),
            "compaction_changed": compaction_plan.get("changed") is True,
            "compaction_dropped": int(_safe_dict(compaction_plan.get("summary")).get("total_dropped") or 0),
            "report_requested": include_report,
        },
        "mechanics_source": MECHANICS_SOURCE,
    }


def run_item_state_maintenance(
    state: dict[str, Any],
    *,
    bucket_limit: int = DEFAULT_BUCKET_LIMIT,
    compaction_threshold: int = DEFAULT_BUCKET_LIMIT,
    record_audit: bool = True,
    record_compaction: bool = True,
    record_report: bool = False,
    report_source: str = "maintenance",
) -> dict[str, Any]:
    """Run audit/compaction/report maintenance against mutable session state."""

    mutable_state = state if isinstance(state, dict) else {}
    before_audit = record_item_state_audit(mutable_state) if record_audit else build_item_state_audit(mutable_state)
    compaction_plan = build_item_state_compaction(mutable_state, bucket_limit=bucket_limit)
    should_compact = _needs_compaction(compaction_plan, threshold=compaction_threshold)
    compaction_result: dict[str, Any] | None = None
    if should_compact:
        compaction_result = apply_item_state_compaction(
            mutable_state,
            bucket_limit=bucket_limit,
            record_trace=record_compaction,
        )
    after_audit = build_item_state_audit(mutable_state) if should_compact else None
    report_result = (
        record_item_report_for_session(mutable_state, source=report_source)
        if record_report
        else None
    )
    summary = _summary(
        before_audit=before_audit,
        compaction=compaction_result,
        after_audit=after_audit,
        report=report_result,
    )
    trace = {
        "event": "item_state_maintained",
        "actions": [
            action
            for action, enabled in (
                ("audit", record_audit),
                ("compact", compaction_result is not None),
                ("report", report_result is not None),
            )
            if enabled
        ],
        "summary": deepcopy(summary),
        "mechanics_source": MECHANICS_SOURCE,
    }
    mechanics = _mechanics(mutable_state)
    mechanics["item_state_maintenance_traces"] = [
        deepcopy(trace),
        *_safe_list(mechanics.get("item_state_maintenance_traces")),
    ][:TRACE_LIMIT]
    mechanics["item_traces"] = [deepcopy(trace), *_safe_list(mechanics.get("item_traces"))][:ITEM_TRACE_LIMIT]
    return {
        "ok": before_audit.get("ok") is True and (compaction_result or compaction_plan).get("ok") is True,
        "audit": before_audit,
        "after_audit": after_audit,
        "compaction_plan": compaction_plan,
        "compaction": compaction_result,
        "report": report_result,
        "summary": summary,
        "trace": trace,
        "mechanics_source": MECHANICS_SOURCE,
    }
