from __future__ import annotations

from html import escape
from typing import Any, Dict, List

from .saved_autoplay_digest_sources import capture_saved_autoplay_digest_sources
from .turn_certification import build_full_100_turn_certification_contract, build_full_100_turn_certification_result
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase7_real_autoplay_certification_artifact_gate"
REPORT_DIAGNOSTICS_SOURCE = "deterministic_phase7_saved_certification_report_diagnostics_gate"
CERTIFICATION_SECTION_MARKER = "<!-- rpg-phase7-real-autoplay-certification -->"
DEFAULT_EXPECTED_TURNS = 100


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _utf8_size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    return len(str(value).encode("utf-8"))


def _first_list(mapping: Dict[str, Any], *keys: str) -> List[Any]:
    for key in keys:
        rows = mapping.get(key)
        if isinstance(rows, list):
            return rows
    return []


def _first_int(mapping: Dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in mapping:
            return _safe_int(mapping.get(key))
    return 0


def _first_text_size(mapping: Dict[str, Any], *keys: str) -> int:
    for key in keys:
        if mapping.get(key) is not None:
            return _utf8_size(mapping.get(key))
    return 0


def _source_entry(kind: str, **fields: Any) -> Dict[str, Any]:
    entry = {"kind": kind, "source": SOURCE}
    entry.update(fields)
    return entry


def _digest_from_capture(capture: Dict[str, Any], key: str, fallback: str) -> str:
    return _safe_str(_safe_dict(capture.get("digests")).get(key) or fallback)


def _html_items(rows: List[Any], *, empty: str = "None") -> str:
    if not rows:
        return f"<li>{escape(empty)}</li>"
    return "".join(f"<li>{escape(_safe_str(row))}</li>" for row in rows)


def _kv_items(mapping: Dict[str, Any], keys: List[str]) -> str:
    rows = []
    for key in keys:
        if key in mapping:
            rows.append(f"{key}: {_safe_str(mapping.get(key))}")
    return _html_items(rows)


def _readiness_diagnostics(result: Dict[str, Any]) -> str:
    readiness = _safe_dict(result.get("readiness_result"))
    progress = _safe_dict(readiness.get("progress_counts"))
    loop = _safe_dict(readiness.get("loop_summary"))
    budget = _safe_dict(readiness.get("budget_summary"))
    readiness_blockers = _safe_list(readiness.get("blockers"))
    readiness_warnings = _safe_list(readiness.get("warnings"))
    return (
        "<h3>Readiness Diagnostics</h3>"
        "<h4>Progress Counts</h4>"
        f"<ul>{_kv_items(progress, ['travel', 'quest', 'economy', 'combat', 'journal'])}</ul>"
        "<h4>Loop Summary</h4>"
        f"<ul>{_kv_items(loop, ['max_repeated_action_streak', 'max_repeated_location_streak', 'max_no_progress_streak', 'distinct_actions', 'distinct_locations', 'source'])}</ul>"
        "<h4>Budget Summary</h4>"
        f"<ul>{_kv_items(budget, ['report_bytes', 'transcript_debug_bytes', 'projected_report_bytes', 'projected_transcript_debug_bytes', 'report_budget_bytes', 'transcript_debug_budget_bytes', 'source'])}</ul>"
        "<h4>Readiness Blockers</h4>"
        f"<ul>{_html_items(readiness_blockers)}</ul>"
        "<h4>Readiness Warnings</h4>"
        f"<ul>{_html_items(readiness_warnings)}</ul>"
    )


def _state_diff_diagnostics(result: Dict[str, Any]) -> str:
    state_diff = _safe_dict(result.get("state_diff"))
    return (
        "<h3>State and Checkpoint Diagnostics</h3>"
        "<h4>Digest Checks</h4>"
        f"<ul>{_html_items(_safe_list(state_diff.get('checks')))}</ul>"
        "<h4>Digest Blockers</h4>"
        f"<ul>{_html_items(_safe_list(state_diff.get('blockers')))}</ul>"
        f"<p><strong>Source:</strong> {escape(_safe_str(state_diff.get('source')))}</p>"
    )


def build_saved_certification_report_diagnostics(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = _safe_dict(payload)
    result = _safe_dict(payload.get("certification_result"))
    readiness = _safe_dict(result.get("readiness_result"))
    state_diff = _safe_dict(result.get("state_diff"))
    return {
        "certification_status": _safe_str(result.get("certification_status")),
        "reason": _safe_str(result.get("reason")),
        "turns": f"{_safe_int(result.get('actual_turns'))}/{_safe_int(result.get('expected_turns'))}",
        "progress_counts": _safe_dict(readiness.get("progress_counts")),
        "loop_summary": _safe_dict(readiness.get("loop_summary")),
        "budget_summary": _safe_dict(readiness.get("budget_summary")),
        "readiness_blockers": _safe_list(readiness.get("blockers")),
        "readiness_warnings": _safe_list(readiness.get("warnings")),
        "digest_checks": _safe_list(state_diff.get("checks")),
        "digest_blockers": _safe_list(state_diff.get("blockers")),
        "blockers": _safe_list(result.get("blockers")),
        "warnings": _safe_list(result.get("warnings")),
        "source": REPORT_DIAGNOSTICS_SOURCE,
    }


def build_real_autoplay_certification_artifact(saved_artifacts: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize saved autoplay/report outputs into the Phase 7.6 certification shape."""

    saved_artifacts = _safe_dict(saved_artifacts)
    report = _safe_dict(saved_artifacts.get("report"))
    transcript = _safe_dict(saved_artifacts.get("transcript"))
    debug = _safe_dict(saved_artifacts.get("debug"))
    checkpoint = _safe_dict(saved_artifacts.get("checkpoint"))
    state = _safe_dict(saved_artifacts.get("state"))
    digest_capture = capture_saved_autoplay_digest_sources(saved_artifacts)

    turns = _first_list(saved_artifacts, "turns", "turn_rows", "transcript_rows")
    if not turns:
        turns = _first_list(transcript, "turns", "turn_rows", "transcript_rows", "rows")
    if not turns:
        turns = _first_list(report, "turns", "turn_rows", "transcript_rows")

    report_bytes = _first_int(saved_artifacts, "report_bytes", "html_report_bytes")
    if not report_bytes:
        report_bytes = _first_int(report, "report_bytes", "html_report_bytes", "bytes")
    if not report_bytes:
        report_bytes = _first_text_size(saved_artifacts, "campaign_report_html", "report_html", "html")
    if not report_bytes:
        report_bytes = _first_text_size(report, "campaign_report_html", "report_html", "html", "body")

    transcript_bytes = _first_int(saved_artifacts, "transcript_debug_bytes", "transcript_bytes", "debug_bytes")
    if not transcript_bytes:
        transcript_bytes = _first_int(transcript, "transcript_debug_bytes", "transcript_bytes", "debug_bytes", "bytes")
    if not transcript_bytes:
        transcript_bytes = _first_text_size(saved_artifacts, "transcript_json", "transcript_text", "debug_json")
    if not transcript_bytes:
        transcript_bytes = _first_text_size(transcript, "json", "text", "body") + _first_text_size(debug, "json", "text", "body")

    normalized = {
        "turns": [_safe_dict(row) for row in turns],
        "report_bytes": report_bytes,
        "transcript_debug_bytes": transcript_bytes,
        "final_checkpoint_digest": _digest_from_capture(
            digest_capture,
            "final_checkpoint_digest",
            _safe_str(
                saved_artifacts.get("final_checkpoint_digest")
                or checkpoint.get("final_checkpoint_digest")
                or state.get("final_checkpoint_digest")
            ),
        ),
        "loaded_checkpoint_digest": _digest_from_capture(
            digest_capture,
            "loaded_checkpoint_digest",
            _safe_str(
                saved_artifacts.get("loaded_checkpoint_digest")
                or checkpoint.get("loaded_checkpoint_digest")
                or state.get("loaded_checkpoint_digest")
            ),
        ),
        "expected_final_checkpoint_digest": _digest_from_capture(
            digest_capture,
            "expected_final_checkpoint_digest",
            _safe_str(
                saved_artifacts.get("expected_final_checkpoint_digest")
                or checkpoint.get("expected_final_checkpoint_digest")
                or state.get("expected_final_checkpoint_digest")
            ),
        ),
        "final_state_digest": _digest_from_capture(
            digest_capture,
            "final_state_digest",
            _safe_str(saved_artifacts.get("final_state_digest") or state.get("final_state_digest")),
        ),
        "loaded_state_digest": _digest_from_capture(
            digest_capture,
            "loaded_state_digest",
            _safe_str(saved_artifacts.get("loaded_state_digest") or state.get("loaded_state_digest")),
        ),
        "expected_final_state_digest": _digest_from_capture(
            digest_capture,
            "expected_final_state_digest",
            _safe_str(saved_artifacts.get("expected_final_state_digest") or state.get("expected_final_state_digest")),
        ),
        "state_diff_source": _safe_str(saved_artifacts.get("state_diff_source") or digest_capture.get("state_diff_source") or SOURCE),
        "digest_source_metadata": _safe_list(digest_capture.get("metadata")),
        "digest_source_capture": digest_capture,
        "artifact_source": _safe_str(saved_artifacts.get("artifact_source") or SOURCE),
    }
    return normalized


def build_saved_100_turn_certification_payload(
    saved_artifacts: Dict[str, Any],
    *,
    expected_turns: int = DEFAULT_EXPECTED_TURNS,
) -> Dict[str, Any]:
    artifact = build_real_autoplay_certification_artifact(saved_artifacts)
    result = build_full_100_turn_certification_result(artifact, expected_turns=expected_turns)
    contract = build_full_100_turn_certification_contract(result)
    payload = {
        "ok": result.get("ok") is True,
        "reason": "phase7_real_autoplay_certification_artifact_ready"
        if result.get("ok") is True
        else "phase7_real_autoplay_certification_artifact_blocked",
        "certification_result": result,
        "certification_contract": contract,
        "normalized_artifact": artifact,
        "source": SOURCE,
    }
    payload["report_diagnostics"] = build_saved_certification_report_diagnostics(payload)
    return payload


def render_saved_100_turn_certification_report_html(payload: Dict[str, Any]) -> str:
    payload = _safe_dict(payload)
    result = _safe_dict(payload.get("certification_result"))
    diagnostics = _safe_dict(payload.get("report_diagnostics")) or build_saved_certification_report_diagnostics(payload)
    status = _safe_str(result.get("certification_status"))
    reason = _safe_str(result.get("reason"))
    turns = f"{_safe_int(result.get('actual_turns'))}/{_safe_int(result.get('expected_turns'))}"
    blockers = _safe_list(result.get("blockers"))
    warnings = _safe_list(result.get("warnings"))
    return (
        f"{CERTIFICATION_SECTION_MARKER}\n"
        '<section id="phase7-real-autoplay-certification">'
        "<h2>Phase 7.7 Real Autoplay Certification</h2>"
        "<h3>Phase 7 Saved Certification Diagnostics</h3>"
        f"<p><strong>Status:</strong> {escape(status)}</p>"
        f"<p><strong>Reason:</strong> {escape(reason)}</p>"
        f"<p><strong>Turns:</strong> {escape(turns)}</p>"
        f"<p><strong>Source:</strong> {escape(SOURCE)}</p>"
        f"<p><strong>Diagnostics source:</strong> {escape(_safe_str(diagnostics.get('source')))}</p>"
        f"{_readiness_diagnostics(result)}"
        f"{_state_diff_diagnostics(result)}"
        "<h3>Certification Blockers</h3>"
        f"<ul>{_html_items(blockers)}</ul>"
        "<h3>Certification Warnings</h3>"
        f"<ul>{_html_items(warnings)}</ul>"
        "</section>"
    )


def append_saved_100_turn_certification_to_campaign_report_html(existing_html: str, payload: Dict[str, Any]) -> str:
    existing_html = _safe_str(existing_html)
    if CERTIFICATION_SECTION_MARKER in existing_html:
        return existing_html
    section = render_saved_100_turn_certification_report_html(payload)
    if "</body>" in existing_html:
        return existing_html.replace("</body>", f"{section}\n</body>", 1)
    return f"{existing_html}\n{section}"


def assert_phase7_real_autoplay_certification_artifact_ready() -> Dict[str, Any]:
    turns = [
        {
            "turn_index": index + 1,
            "action_text": f"artifact travel step {index % 7}",
            "location_id": f"location:{index % 5}",
            "quest_events": [{"quest_id": "quest:old_mill"}] if index % 25 == 0 else [],
            "currency_delta": {"silver": -1} if index % 20 == 0 else {},
            "journal_updates": ["new clue"] if index % 30 == 0 else [],
            "combat_event": {"encounter_id": "encounter:road"} if index == 40 else None,
        }
        for index in range(DEFAULT_EXPECTED_TURNS)
    ]
    saved = {
        "transcript": {"rows": turns, "text": "turn transcript"},
        "report": {"html": "<html><body>campaign report</body></html>"},
        "checkpoint": {
            "final_checkpoint_digest": "digest:phase7:artifact",
            "loaded_checkpoint_digest": "digest:phase7:artifact",
        },
        "artifact_source": SOURCE,
    }
    payload = build_saved_100_turn_certification_payload(saved)
    rendered = render_saved_100_turn_certification_report_html(payload)
    appended = append_saved_100_turn_certification_to_campaign_report_html("<html><body></body></html>", payload)
    blockers: List[Dict[str, Any]] = []
    if payload.get("ok") is not True:
        blockers.append(_source_entry("real_artifact_certification_payload_not_ok"))
    if CERTIFICATION_SECTION_MARKER not in rendered or CERTIFICATION_SECTION_MARKER not in appended:
        blockers.append(_source_entry("missing_real_artifact_certification_report_section"))
    if "Readiness Diagnostics" not in rendered or "State and Checkpoint Diagnostics" not in rendered:
        blockers.append(_source_entry("missing_saved_certification_report_diagnostics"))
    return {
        "ok": not blockers,
        "reason": "phase7_real_autoplay_certification_artifact_gate_ready"
        if not blockers
        else "phase7_real_autoplay_certification_artifact_gate_not_ready",
        "payload": payload,
        "blockers": blockers,
        "source": SOURCE,
    }
