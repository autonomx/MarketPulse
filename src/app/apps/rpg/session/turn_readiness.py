from __future__ import annotations

from typing import Any, Dict, List, cast
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase7_100_turn_readiness_gate"
DEFAULT_EXPECTED_TURNS = 100
REPORT_BUDGET_BYTES = 5_000_000
TRANSCRIPT_BUDGET_BYTES = 10_000_000
ACTION_LOOP_WARNING = 8
LOCATION_LOOP_WARNING = 12
NO_PROGRESS_WARNING = 10


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _event_text(turn: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = _safe_str(turn.get(key)).strip()
        if text:
            return text
    return ""


def _max_streak(values: List[str]) -> int:
    best = 0
    current = 0
    previous = object()
    for value in values:
        if value and value == previous:
            current += 1
        else:
            current = 1 if value else 0
        previous = value
        best = max(best, current)
    return best


def _progress_flags(turn: Dict[str, Any], *, previous_location: str = "") -> Dict[str, bool]:
    current_location = _event_text(turn, "location_id", "current_location_id")
    destination = _event_text(turn, "destination_id")
    travel_changed = bool(turn.get("travel_event") or turn.get("location_changed"))
    if current_location and previous_location and current_location != previous_location:
        travel_changed = True
    return {
        "travel": bool(destination or travel_changed),
        "quest": bool(_safe_list(turn.get("quest_events")) or _safe_dict(turn.get("quest_delta"))),
        "economy": bool(_safe_dict(turn.get("currency_delta")) or _safe_list(turn.get("inventory_delta"))),
        "combat": bool(turn.get("combat_event") or turn.get("encounter_event")),
        "journal": bool(_safe_list(turn.get("journal_updates")) or turn.get("objective_changed")),
    }


def _progress_rows(turns: List[Dict[str, Any]]) -> List[Dict[str, bool]]:
    rows = []
    previous_location = ""
    for turn in turns:
        rows.append(_progress_flags(turn, previous_location=previous_location))
        current_location = _event_text(turn, "location_id", "current_location_id")
        if current_location:
            previous_location = current_location
    return rows


def _no_progress_streak(progress_rows: List[Dict[str, bool]]) -> int:
    best = 0
    current = 0
    for flags in progress_rows:
        if any(flags.values()):
            current = 0
        else:
            current += 1
        best = max(best, current)
    return best


def _projection(total: int, actual_turns: int, expected_turns: int) -> int:
    if actual_turns <= 0:
        return int(total)
    return int((int(total) / actual_turns) * expected_turns)


def build_100_turn_readiness_result(
    turns: List[Dict[str, Any]],
    *,
    expected_turns: int = DEFAULT_EXPECTED_TURNS,
    report_bytes: int = 0,
    transcript_debug_bytes: int = 0,
) -> Dict[str, Any]:
    turns = [_safe_dict(turn) for turn in _safe_list(turns)]
    expected_turns = _safe_int(expected_turns, DEFAULT_EXPECTED_TURNS) or DEFAULT_EXPECTED_TURNS
    actual_turns = len(turns)
    actions = [_event_text(turn, "action", "action_text", "command_text") for turn in turns]
    locations = [_event_text(turn, "location_id", "current_location_id") for turn in turns]
    progress_rows = _progress_rows(turns)
    progress_counts = {key: 0 for key in ("travel", "quest", "economy", "combat", "journal")}
    for flags in progress_rows:
        for key, value in flags.items():
            if value:
                progress_counts[key] += 1

    loop_summary = {
        "max_repeated_action_streak": _max_streak(actions),
        "max_repeated_location_streak": _max_streak(locations),
        "max_no_progress_streak": _no_progress_streak(progress_rows),
        "distinct_actions": len({action for action in actions if action}),
        "distinct_locations": len({location for location in locations if location}),
        "source": SOURCE,
    }
    budget_summary = {
        "report_bytes": _safe_int(report_bytes),
        "transcript_debug_bytes": _safe_int(transcript_debug_bytes),
        "projected_report_bytes": _projection(report_bytes, actual_turns, expected_turns),
        "projected_transcript_debug_bytes": _projection(transcript_debug_bytes, actual_turns, expected_turns),
        "report_budget_bytes": REPORT_BUDGET_BYTES,
        "transcript_debug_budget_bytes": TRANSCRIPT_BUDGET_BYTES,
        "source": SOURCE,
    }

    blockers = []
    warnings = []
    if actual_turns < expected_turns:
        blockers.append({"kind": "incomplete_turn_count", "actual": actual_turns, "expected": expected_turns, "source": SOURCE})
    if cast(int, budget_summary["projected_report_bytes"]) > REPORT_BUDGET_BYTES:
        blockers.append({"kind": "report_growth_budget_exceeded", "source": SOURCE})
    if cast(int, budget_summary["projected_transcript_debug_bytes"]) > TRANSCRIPT_BUDGET_BYTES:
        blockers.append({"kind": "transcript_debug_growth_budget_exceeded", "source": SOURCE})
    if cast(int, loop_summary["max_repeated_action_streak"]) >= ACTION_LOOP_WARNING:
        warnings.append({"kind": "repeated_action_loop_risk", "severity": "advisory", "source": SOURCE})
    if cast(int, loop_summary["max_repeated_location_streak"]) >= LOCATION_LOOP_WARNING:
        warnings.append({"kind": "repeated_location_loop_risk", "severity": "advisory", "source": SOURCE})
    if cast(int, loop_summary["max_no_progress_streak"]) >= NO_PROGRESS_WARNING:
        warnings.append({"kind": "no_progress_loop_risk", "severity": "advisory", "source": SOURCE})
    if not any(progress_counts.values()):
        warnings.append({"kind": "no_progress_signals_detected", "severity": "advisory", "source": SOURCE})

    return {
        "ok": not blockers,
        "reason": "phase7_100_turn_readiness_validated" if not blockers else "phase7_100_turn_readiness_blocked",
        "expected_turns": expected_turns,
        "actual_turns": actual_turns,
        "progress_counts": progress_counts,
        "loop_summary": loop_summary,
        "budget_summary": budget_summary,
        "blockers": blockers,
        "warnings": warnings,
        "source": SOURCE,
    }


def build_100_turn_readiness_contract(result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(result)
    return {
        "source": SOURCE,
        "allowed_readiness_claims": [
            f"Readiness result: {_safe_str(result.get('reason'))}",
            f"Turns analyzed: {_safe_int(result.get('actual_turns'))}/{_safe_int(result.get('expected_turns'))}",
            f"Blocker count: {len(_safe_list(result.get('blockers')))}",
            f"Warning count: {len(_safe_list(result.get('warnings')))}",
        ],
        "forbidden_readiness_claims": [
            "Provider and LLM calls are outside deterministic 100-turn readiness analysis.",
            "Advisory loop warnings must not mutate gameplay state.",
            "Report growth projections must not be ignored when they exceed hard budgets.",
            "Do not claim final 100-turn certification from this advisory scaffold alone.",
        ],
    }


def assert_phase7_100_turn_readiness_ready() -> Dict[str, Any]:
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
            }
        )
    result = build_100_turn_readiness_result(turns, report_bytes=250_000, transcript_debug_bytes=500_000)
    contract = build_100_turn_readiness_contract(result)
    blockers = list(_safe_list(result.get("blockers")))
    if result.get("ok") is not True:
        blockers.append({"kind": "readiness_not_validated", "source": SOURCE})
    if not contract.get("forbidden_readiness_claims"):
        blockers.append({"kind": "missing_readiness_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase7_100_turn_readiness_gate_ready" if not blockers else "phase7_100_turn_readiness_gate_not_ready",
        "result": result,
        "contract": contract,
        "blockers": blockers,
        "source": SOURCE,
    }
