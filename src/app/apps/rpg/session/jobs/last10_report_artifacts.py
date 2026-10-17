"""Artifact writers for the RPG last-ten-turn debug report."""
from __future__ import annotations

import html
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.paths import test_results_root


def write_rpg_last10_report(payload: dict[str, Any], *, output_root: Path | None = None) -> dict[str, Any]:
    payload = dict(payload)
    root = output_root or (test_results_root() / "rpg-last10-turn-report")
    session_slug = _safe_slug(_text(payload.get("session_id")) or "no-session")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = root / f"{timestamp}-{session_slug}"
    run_dir.mkdir(parents=True, exist_ok=True)

    summary_path = run_dir / "rpg-last10-turn-report-summary.json"
    performance_path = run_dir / "rpg-last10-turn-performance.json"
    transcript_path = run_dir / "rpg-last10-turn-transcript.json"
    html_path = run_dir / "rpg-last10-turn-report.html"
    zip_path = run_dir / "rpg-last10-turn-report.zip"
    summary = {
        "format_version": payload.get("format_version"),
        "report_kind": payload.get("report_kind"),
        "generated_at": payload.get("generated_at"),
        "session_id": payload.get("session_id"),
        "requested_turn_limit": payload.get("requested_turn_limit"),
        "turn_count": payload.get("turn_count"),
        "session_event_count": payload.get("session_event_count"),
        "performance": payload.get("performance"),
        "diagnostics": payload.get("diagnostics"),
        "debug_payloads_in_transcript": True,
        "summary_path": str(summary_path),
        "performance_path": str(performance_path),
        "transcript_path": str(transcript_path),
        "html_report_path": str(html_path),
        "zip_path": str(zip_path),
    }
    payload["summary"] = summary
    _write_json(summary_path, summary)
    _write_json(performance_path, payload.get("performance"))
    _write_json(
        transcript_path,
        {
            "format_version": payload.get("format_version"),
            "session_id": payload.get("session_id"),
            "turns": payload.get("turns"),
            "session_events": payload.get("session_events"),
        },
    )
    html_path.write_text(render_rpg_last10_report_html(payload), encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in (summary_path, performance_path, transcript_path, html_path):
            archive.write(path, arcname=path.name)
    return payload


def render_rpg_last10_report_html(payload: dict[str, Any]) -> str:
    summary = _dict_value(payload.get("summary"))
    performance = _dict_value(payload.get("performance"))
    diagnostics = _list_value(payload.get("diagnostics"))
    return "\n".join(
        [
            "<!doctype html>",
            "<html lang=\"en\">",
            "<head>",
            "<meta charset=\"utf-8\" />",
            "<title>RPG Last 10 Turn Debug Report</title>",
            "<style>body{font-family:system-ui,sans-serif;line-height:1.45;margin:2rem;max-width:1200px}table{border-collapse:collapse;width:100%;margin:1rem 0}td,th{border:1px solid #ccc;padding:.45rem;text-align:left;vertical-align:top}code,pre{background:#f4f4f4;padding:.2rem .35rem}pre{overflow:auto;padding:1rem}.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.75rem}.metric{border:1px solid #ddd;border-radius:8px;padding:.75rem}details{margin:.35rem 0}summary{cursor:pointer;font-weight:600}</style>",
            "</head>",
            "<body>",
            "<h1>RPG Last 10 Turn Debug Report</h1>",
            f"<p><strong>Session:</strong> {_escape(payload.get('session_id')) or 'No session'} · <strong>Generated:</strong> {_escape(payload.get('generated_at'))}</p>",
            "<h2>Performance metrics</h2>",
            _performance_html(performance),
            "<h2>Last turn jobs</h2>",
            _turns_html(_list_value(payload.get("turns"))),
            "<h2>Turn response records</h2>",
            _turn_debug_html(_list_value(payload.get("turns"))),
            "<h2>Session event fallback</h2>",
            _events_html(_list_value(payload.get("session_events"))),
            "<h2>Diagnostics</h2>",
            _diagnostics_html(diagnostics),
            "<h2>Artifact paths</h2>",
            f"<pre>{_escape(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True, default=str))}</pre>",
            "</body>",
            "</html>",
        ]
    )


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")


def _performance_html(performance: dict[str, Any]) -> str:
    keys = [
        "turn_count",
        "measured_turn_count",
        "total_turn_seconds",
        "avg_turn_seconds",
        "p95_turn_seconds",
        "min_turn_seconds",
        "max_turn_seconds",
        "provider_observed_turn_count",
    ]
    cards = [f"<div class=\"metric\"><strong>{_escape(key)}</strong><br />{_escape(performance.get(key))}</div>" for key in keys]
    return f"<div class=\"metric-grid\">{''.join(cards)}</div><h3>Stage totals</h3><pre>{_escape(json.dumps(performance.get('stage_seconds_totals') or {}, indent=2, sort_keys=True))}</pre>"


def _turns_html(turns: list[Any]) -> str:
    if not turns:
        return "<p>No completed turn jobs were found for this session.</p>"
    rows = []
    for raw in turns:
        row = _dict_value(raw)
        selection = _dict_value(row.get("response_selection_trace"))
        timing = _dict_value(_dict_value(row.get("performance_trace")).get("job_timing_seconds"))
        rows.append(
            "<tr>"
            f"<td>{_escape(row.get('sequence'))}</td>"
            f"<td>{_escape(row.get('command'))}</td>"
            f"<td>{_escape(row.get('response'))}</td>"
            f"<td>{_escape(row.get('duration_seconds'))}</td>"
            f"<td>{_escape(selection.get('selected_response_source') or selection.get('narration_source') or selection.get('fallback_source'))}</td>"
            f"<td>{_escape(selection.get('fallback_reason'))}</td>"
            f"<td>{_escape(timing.get('started_to_completed'))}</td>"
            f"<td>{_escape(row.get('job_id'))}</td>"
            "</tr>"
        )
    return "<table><thead><tr><th>#</th><th>Command</th><th>Response</th><th>Seconds</th><th>Source</th><th>Fallback</th><th>Run seconds</th><th>Job</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def _turn_debug_html(turns: list[Any]) -> str:
    if not turns:
        return "<p>No turn response records were found.</p>"
    sections = []
    for raw in turns:
        row = _dict_value(raw)
        debug = {
            "raw_intent_diagnostics": row.get("raw_intent_diagnostics"),
            "dialogue_payload": row.get("dialogue_payload"),
            "response_selection_trace": row.get("response_selection_trace"),
            "performance_trace": row.get("performance_trace"),
            "turn_response_record": row.get("turn_response_record"),
            "raw_input_payload": row.get("raw_input_payload"),
            "raw_output_refs": row.get("raw_output_refs"),
            "raw_logs": row.get("raw_logs"),
        }
        label = f"Turn {row.get('sequence')} · {row.get('command') or row.get('job_id')}"
        sections.append(
            f"<details><summary>{_escape(label)}</summary><pre>{_escape(json.dumps(debug, indent=2, ensure_ascii=False, sort_keys=True, default=str))}</pre></details>"
        )
    return "".join(sections)


def _events_html(events: list[Any]) -> str:
    if not events:
        return "<p>No session event fallback rows were found.</p>"
    rows = []
    for raw in events:
        row = _dict_value(raw)
        rows.append(
            "<tr>"
            f"<td>{_escape(row.get('sequence'))}</td>"
            f"<td>{_escape(row.get('title'))}</td>"
            f"<td>{_escape(row.get('command'))}</td>"
            f"<td>{_escape(row.get('detail'))}</td>"
            "</tr>"
        )
    return "<table><thead><tr><th>#</th><th>Title</th><th>Command</th><th>Detail</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def _diagnostics_html(diagnostics: list[Any]) -> str:
    if not diagnostics:
        return "<p>No diagnostics were emitted.</p>"
    items = []
    for raw in diagnostics:
        row = _dict_value(raw)
        items.append(f"<li><strong>{_escape(row.get('kind'))}</strong>: {_escape(row.get('message'))}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-._")
    return slug[:80] or "session"


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
