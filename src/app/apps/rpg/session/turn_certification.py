from __future__ import annotations

from typing import Any, Dict, List

from .turn_readiness import build_100_turn_readiness_result
from .turn_readiness_report import build_100_turn_readiness_report_payload
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase7_full_100_turn_certification_gate"
READINESS_SOURCE = "deterministic_phase7_100_turn_readiness_gate"
REPORT_SOURCE = "deterministic_phase7_100_turn_readiness_report_gate"
DEFAULT_EXPECTED_TURNS = 100


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _source_entry(kind: str, *, source: str = SOURCE, **fields: Any) -> Dict[str, Any]:
    entry = {"kind": kind, "source": source}
    entry.update(fields)
    return entry


def _artifact_turns(artifact: Dict[str, Any]) -> List[Dict[str, Any]]:
    turns = artifact.get("turns")
    if turns is None:
        turns = artifact.get("turn_rows")
    if turns is None:
        turns = artifact.get("transcript_rows")
    return [_safe_dict(row) for row in _safe_list(turns)]


def _artifact_int(artifact: Dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in artifact:
            return _safe_int(artifact.get(key))
    return 0


def _digest_source(artifact: Dict[str, Any], digest_key: str, fallback_source: str) -> str:
    metadata = artifact.get("digest_source_metadata") or artifact.get("digest_metadata")
    for row in _safe_list(metadata):
        row_dict = _safe_dict(row)
        if row_dict.get("kind") == digest_key:
            return _safe_str(row_dict.get("source") or fallback_source)
    return fallback_source


def _add_digest_check(
    checks: List[Dict[str, Any]],
    blockers: List[Dict[str, Any]],
    *,
    kind: str,
    mismatch_kind: str,
    left_digest: str,
    right_digest: str,
    source: str,
) -> None:
    if not left_digest or not right_digest:
        return
    ok = left_digest == right_digest
    checks.append({"kind": kind, "ok": ok, "source": source})
    if not ok:
        blockers.append(_source_entry(mismatch_kind, source=source))


def _state_diff_result(artifact: Dict[str, Any]) -> Dict[str, Any]:
    source = _safe_str(artifact.get("state_diff_source") or SOURCE)
    final_checkpoint_digest = _safe_str(artifact.get("final_checkpoint_digest"))
    loaded_checkpoint_digest = _safe_str(artifact.get("loaded_checkpoint_digest"))
    expected_checkpoint_digest = _safe_str(artifact.get("expected_final_checkpoint_digest"))
    final_state_digest = _safe_str(artifact.get("final_state_digest"))
    loaded_state_digest = _safe_str(artifact.get("loaded_state_digest"))
    expected_state_digest = _safe_str(artifact.get("expected_final_state_digest"))
    checks: List[Dict[str, Any]] = []
    blockers: List[Dict[str, Any]] = []
    _add_digest_check(
        checks,
        blockers,
        kind="final_vs_loaded_checkpoint_digest",
        mismatch_kind="final_vs_loaded_checkpoint_digest_mismatch",
        left_digest=final_checkpoint_digest,
        right_digest=loaded_checkpoint_digest,
        source=_digest_source(artifact, "loaded_checkpoint_digest", source),
    )
    _add_digest_check(
        checks,
        blockers,
        kind="final_vs_expected_checkpoint_digest",
        mismatch_kind="final_vs_expected_checkpoint_digest_mismatch",
        left_digest=final_checkpoint_digest,
        right_digest=expected_checkpoint_digest,
        source=_digest_source(artifact, "expected_final_checkpoint_digest", source),
    )
    _add_digest_check(
        checks,
        blockers,
        kind="final_vs_loaded_state_digest",
        mismatch_kind="final_vs_loaded_state_digest_mismatch",
        left_digest=final_state_digest,
        right_digest=loaded_state_digest,
        source=_digest_source(artifact, "loaded_state_digest", source),
    )
    _add_digest_check(
        checks,
        blockers,
        kind="final_vs_expected_state_digest",
        mismatch_kind="final_vs_expected_state_digest_mismatch",
        left_digest=final_state_digest,
        right_digest=expected_state_digest,
        source=_digest_source(artifact, "expected_final_state_digest", source),
    )
    return {"checked": bool(checks), "checks": checks, "blockers": blockers, "source": source}


def build_full_100_turn_certification_result(
    artifact: Dict[str, Any],
    *,
    expected_turns: int = DEFAULT_EXPECTED_TURNS,
) -> Dict[str, Any]:
    artifact = _safe_dict(artifact)
    expected_turns = _safe_int(expected_turns, DEFAULT_EXPECTED_TURNS) or DEFAULT_EXPECTED_TURNS
    turns = _artifact_turns(artifact)
    report_bytes = _artifact_int(artifact, "report_bytes", "html_report_bytes")
    transcript_bytes = _artifact_int(artifact, "transcript_debug_bytes", "transcript_bytes", "debug_bytes")
    readiness = build_100_turn_readiness_result(
        turns,
        expected_turns=expected_turns,
        report_bytes=report_bytes,
        transcript_debug_bytes=transcript_bytes,
    )
    report_payload = build_100_turn_readiness_report_payload(readiness)
    state_diff = _state_diff_result(artifact)
    blockers = []
    warnings = []
    if len(turns) != expected_turns:
        blockers.append(_source_entry("artifact_turn_count_not_exact", actual=len(turns), expected=expected_turns))
    for row in _safe_list(report_payload.get("critical_blockers")):
        row_dict = _safe_dict(row)
        blockers.append(
            _source_entry(
                "readiness_critical_blocker",
                source=_safe_str(row_dict.get("source") or READINESS_SOURCE),
                blocker=_safe_str(row_dict.get("kind")),
            )
        )
    blockers.extend(_safe_list(state_diff.get("blockers")))
    if not _safe_dict(report_payload.get("severity_counts")):
        blockers.append(_source_entry("missing_report_severity_counts", source=REPORT_SOURCE))
    for row in _safe_list(report_payload.get("warnings")):
        row_dict = _safe_dict(row)
        warnings.append(
            _source_entry(
                "readiness_warning",
                source=_safe_str(row_dict.get("source") or REPORT_SOURCE),
                warning=_safe_str(row_dict.get("kind")),
            )
        )
    for row in _safe_list(report_payload.get("advisories")):
        row_dict = _safe_dict(row)
        if row_dict.get("kind") != "advisory_until_full_100_turn_autoplay_gate":
            warnings.append(
                _source_entry(
                    "readiness_advisory",
                    source=_safe_str(row_dict.get("source") or REPORT_SOURCE),
                    advisory=_safe_str(row_dict.get("kind")),
                )
            )
    ok = not blockers
    certification_status = "final_100_turn_certification_passed" if ok else "final_100_turn_certification_blocked"
    return {
        "ok": ok,
        "reason": "phase7_full_100_turn_certification_passed" if ok else "phase7_full_100_turn_certification_blocked",
        "certification_status": certification_status,
        "expected_turns": expected_turns,
        "actual_turns": len(turns),
        "readiness_result": readiness,
        "readiness_report_payload": report_payload,
        "state_diff": state_diff,
        "blockers": blockers,
        "warnings": warnings,
        "source": SOURCE,
    }


def build_full_100_turn_certification_contract(result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(result)
    return {
        "source": SOURCE,
        "allowed_certification_claims": [
            f"Certification result: {_safe_str(result.get('reason'))}",
            f"Certification status: {_safe_str(result.get('certification_status'))}",
            f"Turns certified: {_safe_int(result.get('actual_turns'))}/{_safe_int(result.get('expected_turns'))}",
            f"Critical blocker count: {len(_safe_list(result.get('blockers')))}",
        ],
        "forbidden_certification_claims": [
            "Do not certify fewer or more than the expected 100 turns.",
            "Do not certify when readiness report critical blockers are present.",
            "Do not ignore save/load or state digest mismatches when provided.",
            "Provider and LLM calls are outside deterministic certification logic.",
            "Certification rendering and checks must not mutate gameplay state.",
        ],
    }


def assert_phase7_full_100_turn_certification_ready() -> Dict[str, Any]:
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
    artifact = {
        "turns": turns,
        "report_bytes": 250_000,
        "transcript_debug_bytes": 500_000,
        "final_checkpoint_digest": "digest:phase7:final",
        "loaded_checkpoint_digest": "digest:phase7:final",
        "final_state_digest": "digest:phase7:state",
        "loaded_state_digest": "digest:phase7:state",
        "state_diff_source": SOURCE,
    }
    result = build_full_100_turn_certification_result(artifact)
    contract = build_full_100_turn_certification_contract(result)
    blockers = list(_safe_list(result.get("blockers")))
    if result.get("ok") is not True:
        blockers.append(_source_entry("certification_not_validated"))
    if result.get("certification_status") != "final_100_turn_certification_passed":
        blockers.append(_source_entry("missing_final_certification_status"))
    if not contract.get("forbidden_certification_claims"):
        blockers.append(_source_entry("missing_certification_contract_guardrails"))
    return {
        "ok": not blockers,
        "reason": "phase7_full_100_turn_certification_gate_ready" if not blockers else "phase7_full_100_turn_certification_gate_not_ready",
        "result": result,
        "contract": contract,
        "blockers": blockers,
        "source": SOURCE,
    }
