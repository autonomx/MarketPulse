from __future__ import annotations

import html
from typing import Any, Dict, List, cast

from .turn_readiness import build_100_turn_readiness_result
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase7_100_turn_readiness_report_gate"
READINESS_SOURCE = "deterministic_phase7_100_turn_readiness_gate"
SECTION_ID = "phase7-100-turn-readiness-report"
PROGRESS_KEYS = ("travel", "quest", "economy", "combat", "journal")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _escape(value: Any) -> str:
    return html.escape(_safe_str(value), quote=True)


def _entry_source(row: Dict[str, Any], *, default_source: str = READINESS_SOURCE) -> str:
    return _safe_str(row.get("source") or default_source)


def _copy_source_entry(row: Any, *, severity: str, default_source: str = READINESS_SOURCE) -> Dict[str, Any]:
    row = _safe_dict(row)
    entry: Dict[str, Any] = {
        "kind": _safe_str(row.get("kind") or "unknown_readiness_entry"),
        "severity": severity,
        "source": _entry_source(row, default_source=default_source),
    }
    for key, value in row.items():
        if key in {"kind", "severity", "source"}:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            entry[key] = value
    return entry


def _warning_report_severity(row: Any) -> str:
    kind = _safe_str(_safe_dict(row).get("kind"))
    if kind == "no_progress_signals_detected":
        return "advisory"
    return "warning"


def _source_dict(value: Any, *, default_source: str = READINESS_SOURCE) -> Dict[str, Any]:
    data = dict(_safe_dict(value))
    data["source"] = _safe_str(data.get("source") or default_source)
    return data


def build_100_turn_readiness_report_payload(readiness_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(readiness_result)
    result_source = _safe_str(result.get("source") or READINESS_SOURCE)
    progress_counts = _safe_dict(result.get("progress_counts"))
    loop_summary = _source_dict(result.get("loop_summary"), default_source=result_source)
    budget_summary = _source_dict(result.get("budget_summary"), default_source=result_source)

    critical_blockers = [
        _copy_source_entry(row, severity="critical", default_source=result_source)
        for row in _safe_list(result.get("blockers"))
    ]
    warnings: List[Dict[str, Any]] = []
    advisories: List[Dict[str, Any]] = [
        {
            "kind": "advisory_until_full_100_turn_autoplay_gate",
            "severity": "advisory",
            "source": SOURCE,
        }
    ]
    for row in _safe_list(result.get("warnings")):
        severity = _warning_report_severity(row)
        entry = _copy_source_entry(row, severity=severity, default_source=result_source)
        if severity == "advisory":
            advisories.append(entry)
        else:
            warnings.append(entry)

    payload = {
        "source": SOURCE,
        "readiness_source": result_source,
        "certification_status": "advisory_not_final_certification",
        "advisory_until_full_100_turn_autoplay_gate": True,
        "turn_count": {
            "actual": _safe_int(result.get("actual_turns")),
            "expected": _safe_int(result.get("expected_turns")),
            "ok": _safe_int(result.get("actual_turns")) >= _safe_int(result.get("expected_turns"), 100),
            "source": result_source,
        },
        "progress_metrics": {
            **{key: _safe_int(progress_counts.get(key)) for key in PROGRESS_KEYS},
            "source": result_source,
        },
        "loop_risks": loop_summary,
        "growth_projections": budget_summary,
        "critical_blockers": critical_blockers,
        "warnings": warnings,
        "advisories": advisories,
        "severity_counts": {
            "critical": len(critical_blockers),
            "warning": len(warnings),
            "advisory": len(advisories),
        },
    }
    payload["ok"] = cast(dict[str, int], payload["severity_counts"])["critical"] == 0
    payload["reason"] = (
        "phase7_100_turn_readiness_report_ready"
        if payload["ok"]
        else "phase7_100_turn_readiness_report_has_critical_blockers"
    )
    return payload


def _render_metric_table(title: str, metrics: Dict[str, Any], keys: List[str]) -> str:
    rows = [f"<h3>{_escape(title)}</h3>", "<table>", "<tbody>"]
    for key in keys:
        rows.append(f"<tr><th>{_escape(key)}</th><td>{_escape(metrics.get(key))}</td></tr>")
    rows.append(f"<tr><th>source</th><td>{_escape(metrics.get('source'))}</td></tr>")
    rows.extend(["</tbody>", "</table>"])
    return "\n".join(rows)


def _render_entries(title: str, entries: List[Dict[str, Any]]) -> str:
    rows = [f"<h3>{_escape(title)}</h3>"]
    if not entries:
        rows.append("<p>None.</p>")
        return "\n".join(rows)
    rows.append("<ul>")
    for entry in entries:
        detail_bits = []
        for key in ("kind", "severity", "actual", "expected", "source"):
            if key in entry:
                detail_bits.append(f"{key}: {_escape(entry.get(key))}")
        rows.append(f"<li>{' | '.join(detail_bits)}</li>")
    rows.append("</ul>")
    return "\n".join(rows)


def render_100_turn_readiness_report_html(readiness_result: Dict[str, Any]) -> str:
    payload = build_100_turn_readiness_report_payload(readiness_result)
    turn_count = _safe_dict(payload.get("turn_count"))
    severity_counts = _safe_dict(payload.get("severity_counts"))
    rows = [
        f'<section id="{SECTION_ID}" data-source="{_escape(payload.get("source"))}">',
        "<h2>100-Turn Readiness</h2>",
        "<p>Advisory readiness report; not final certification until a full 100-turn autoplay gate passes.</p>",
        "<dl>",
        f"<dt>Status</dt><dd>{_escape(payload.get('reason'))}</dd>",
        f"<dt>Certification</dt><dd>{_escape(payload.get('certification_status'))}</dd>",
        f"<dt>Turns</dt><dd>{_escape(turn_count.get('actual'))}/{_escape(turn_count.get('expected'))}</dd>",
        f"<dt>Critical</dt><dd>{_escape(severity_counts.get('critical'))}</dd>",
        f"<dt>Warning</dt><dd>{_escape(severity_counts.get('warning'))}</dd>",
        f"<dt>Advisory</dt><dd>{_escape(severity_counts.get('advisory'))}</dd>",
        "</dl>",
        _render_metric_table("Progress Metrics", _safe_dict(payload.get("progress_metrics")), list(PROGRESS_KEYS)),
        _render_metric_table(
            "Loop Risks",
            _safe_dict(payload.get("loop_risks")),
            ["max_repeated_action_streak", "max_repeated_location_streak", "max_no_progress_streak", "distinct_actions", "distinct_locations"],
        ),
        _render_metric_table(
            "Growth Projections",
            _safe_dict(payload.get("growth_projections")),
            ["projected_report_bytes", "report_budget_bytes", "projected_transcript_debug_bytes", "transcript_debug_budget_bytes"],
        ),
        _render_entries("Critical Blockers", _safe_list(payload.get("critical_blockers"))),
        _render_entries("Warnings", _safe_list(payload.get("warnings"))),
        _render_entries("Advisories", _safe_list(payload.get("advisories"))),
        "</section>",
    ]
    return "\n".join(rows)


def append_100_turn_readiness_report_to_campaign_report_html(report_html: str, readiness_result: Dict[str, Any]) -> str:
    report_html = _safe_str(report_html)
    if f'id="{SECTION_ID}"' in report_html or f"id='{SECTION_ID}'" in report_html:
        return report_html
    section = render_100_turn_readiness_report_html(readiness_result)
    if "</main>" in report_html:
        return report_html.replace("</main>", f"{section}\n</main>", 1)
    if "</body>" in report_html:
        return report_html.replace("</body>", f"{section}\n</body>", 1)
    return f"{report_html}\n{section}"


def build_100_turn_readiness_report_contract(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = _safe_dict(payload)
    severity_counts = _safe_dict(payload.get("severity_counts"))
    return {
        "source": SOURCE,
        "allowed_readiness_claims": [
            f"Readiness report result: {_safe_str(payload.get('reason'))}",
            f"Critical blockers: {_safe_int(severity_counts.get('critical'))}",
            f"Warnings: {_safe_int(severity_counts.get('warning'))}",
            f"Advisories: {_safe_int(severity_counts.get('advisory'))}",
        ],
        "forbidden_readiness_claims": [
            "Do not claim final 100-turn certification from this advisory report.",
            "Provider and LLM calls are outside deterministic readiness report rendering.",
            "Report rendering must not mutate gameplay or readiness state.",
            "Readiness blocker and warning entries must keep source fields.",
            "Readiness HTML must be escaped and safe.",
        ],
    }


def assert_phase7_100_turn_readiness_report_ready() -> Dict[str, Any]:
    turns = []
    for index in range(100):
        turns.append(
            {
                "turn_index": index + 1,
                "action_text": f"travel step {index % 5}",
                "location_id": f"location:{index % 4}",
                "quest_events": [{"quest_id": "quest:old_mill"}] if index % 25 == 0 else [],
                "currency_delta": {"silver": -1} if index % 20 == 0 else {},
                "journal_updates": ["new clue"] if index % 30 == 0 else [],
                "combat_event": {"encounter_id": "encounter:road"} if index == 40 else None,
            }
        )
    readiness = build_100_turn_readiness_result(turns, report_bytes=250_000, transcript_debug_bytes=500_000)
    payload = build_100_turn_readiness_report_payload(readiness)
    report_html = render_100_turn_readiness_report_html(readiness)
    contract = build_100_turn_readiness_report_contract(payload)
    blockers = []
    if payload.get("severity_counts", {}).get("critical"):
        blockers.append({"kind": "unexpected_critical_readiness_blockers", "source": SOURCE})
    if f'id="{SECTION_ID}"' not in report_html:
        blockers.append({"kind": "missing_readiness_report_section", "source": SOURCE})
    if "not final certification" not in report_html:
        blockers.append({"kind": "missing_advisory_certification_guardrail", "source": SOURCE})
    if not contract.get("forbidden_readiness_claims"):
        blockers.append({"kind": "missing_report_contract_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase7_100_turn_readiness_report_gate_ready" if not blockers else "phase7_100_turn_readiness_report_gate_not_ready",
        "readiness_result": readiness,
        "payload": payload,
        "html": report_html,
        "contract": contract,
        "blockers": blockers,
        "source": SOURCE,
    }
