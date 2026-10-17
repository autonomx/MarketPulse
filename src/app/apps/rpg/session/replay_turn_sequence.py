from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, List

from app.apps.rpg.session.replay_checkpoint import (
    build_session_checkpoint,
    compare_session_checkpoints,
    restore_session_from_checkpoint,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase7_replay_turn_sequence_validation"

CommandHandler = Callable[[Dict[str, Any], Dict[str, Any], int], Dict[str, Any]]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _travel_handler(simulation_state: Dict[str, Any], command: Dict[str, Any], turn_index: int) -> Dict[str, Any]:
    from app.apps.rpg.world.locations.command_routing import apply_runtime_travel_command

    return apply_runtime_travel_command(
        simulation_state,
        _safe_str(command.get("command_text")),
        turn_index=turn_index,
        encounter_seed=_safe_str(command.get("encounter_seed")) or "phase7.2",
        roll_encounter=bool(command.get("roll_encounter", False)),
    )


def _observe_handler(simulation_state: Dict[str, Any], command: Dict[str, Any], turn_index: int) -> Dict[str, Any]:
    return {
        "ok": True,
        "reason": "observation_recorded",
        "turn_index": int(turn_index or 0),
        "command_text": _safe_str(command.get("command_text")),
        "source": SOURCE,
    }


def default_replay_command_handlers() -> Dict[str, CommandHandler]:
    return {
        "travel": _travel_handler,
        "observe": _observe_handler,
    }


def _checkpoint_session(session: Dict[str, Any], simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    replay_session = deepcopy(_safe_dict(session))
    replay_session["simulation_state"] = deepcopy(simulation_state)
    return replay_session


def _apply_replay_command(
    simulation_state: Dict[str, Any],
    command: Dict[str, Any],
    turn_index: int,
    handlers: Dict[str, CommandHandler],
) -> Dict[str, Any]:
    command_type = _safe_str(command.get("type") or command.get("command_type") or "observe")
    handler = handlers.get(command_type)
    if handler is None:
        return {
            "ok": False,
            "reason": "unknown_replay_command_type",
            "command_type": command_type,
            "turn_index": int(turn_index or 0),
            "source": SOURCE,
        }
    return handler(simulation_state, command, turn_index)


def run_replay_turn_sequence(
    checkpoint: Dict[str, Any],
    commands: List[Dict[str, Any]],
    *,
    label: str = "replay",
    command_handlers: Dict[str, CommandHandler] | None = None,
) -> Dict[str, Any]:
    restored = restore_session_from_checkpoint(checkpoint)
    if restored.get("ok") is not True:
        return {"ok": False, "reason": "checkpoint_restore_failed", "restore_result": restored, "source": SOURCE}
    session = deepcopy(_safe_dict(restored.get("session")))
    simulation_state = deepcopy(_safe_dict(session.get("simulation_state")))
    handlers = {**default_replay_command_handlers(), **_safe_dict(command_handlers)}
    start = build_session_checkpoint(_checkpoint_session(session, simulation_state), label=f"{label}:start", turn_index=0)
    command_results = []
    checkpoints = [start]
    for index, command in enumerate(_safe_list(commands), start=1):
        command_dict = _safe_dict(command)
        result = _apply_replay_command(simulation_state, command_dict, index, handlers)
        command_results.append(
            {
                "turn_index": index,
                "command_type": _safe_str(command_dict.get("type") or command_dict.get("command_type") or "observe"),
                "command_text": _safe_str(command_dict.get("command_text")),
                "ok": result.get("ok") is True,
                "reason": _safe_str(result.get("reason")),
                "result_source": _safe_str(result.get("source")),
                "source": SOURCE,
            }
        )
        checkpoints.append(
            build_session_checkpoint(
                _checkpoint_session(session, simulation_state),
                label=f"{label}:turn:{index}",
                turn_index=index,
            )
        )
    final_checkpoint = checkpoints[-1]
    return {
        "ok": True,
        "reason": "replay_turn_sequence_completed",
        "command_count": len(_safe_list(commands)),
        "command_results": command_results,
        "start_checkpoint": start,
        "final_checkpoint": final_checkpoint,
        "checkpoint_digests": [row.get("digest") for row in checkpoints],
        "source": SOURCE,
    }


def validate_replay_turn_sequence(
    checkpoint: Dict[str, Any],
    commands: List[Dict[str, Any]],
    *,
    expected_final_checkpoint: Dict[str, Any] | None = None,
    label: str = "replay",
) -> Dict[str, Any]:
    first = run_replay_turn_sequence(checkpoint, commands, label=f"{label}:first")
    second = run_replay_turn_sequence(checkpoint, commands, label=f"{label}:second")
    if first.get("ok") is not True or second.get("ok") is not True:
        return {
            "ok": False,
            "reason": "replay_turn_sequence_failed",
            "first": first,
            "second": second,
            "source": SOURCE,
        }
    comparison = compare_session_checkpoints(
        _safe_dict(first.get("final_checkpoint")),
        _safe_dict(second.get("final_checkpoint")),
    )
    expected_comparison = None
    if expected_final_checkpoint is not None:
        expected_comparison = compare_session_checkpoints(
            _safe_dict(expected_final_checkpoint),
            _safe_dict(first.get("final_checkpoint")),
        )
    expected_match = expected_comparison is None or expected_comparison.get("deterministic_match") is True
    ok = comparison.get("deterministic_match") is True and expected_match
    return {
        "ok": ok,
        "reason": "replay_turn_sequence_validated" if ok else "replay_turn_sequence_drift_detected",
        "deterministic_match": comparison.get("deterministic_match") is True,
        "expected_match": expected_match,
        "comparison": comparison,
        "expected_comparison": expected_comparison,
        "first": first,
        "second": second,
        "source": SOURCE,
    }


def build_replay_turn_sequence_contract(validation_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(validation_result)
    comparison = _safe_dict(result.get("comparison"))
    return {
        "source": SOURCE,
        "allowed_replay_claims": [
            f"Replay result: {_safe_str(result.get('reason'))}",
            f"Deterministic match: {comparison.get('deterministic_match') is True}",
            f"Before digest: {_safe_str(comparison.get('before_digest'))}",
            f"After digest: {_safe_str(comparison.get('after_digest'))}",
        ],
        "forbidden_replay_claims": [
            "Do not call providers or LLMs while replaying deterministic turn sequences.",
            "Do not bypass canonical runtime command helpers for replayed gameplay commands.",
            "Do not ignore checkpoint digest drift or hidden state mutation.",
            "Do not treat rejected commands as successful state changes.",
        ],
    }


def assert_phase7_replay_turn_sequence_ready() -> Dict[str, Any]:
    from app.apps.rpg.world.locations.discovery import discover_location, discover_route, unblock_route

    session = {
        "manifest": {"id": "phase7:sequence", "session_id": "phase7:sequence"},
        "installed_packs": ["base"],
        "simulation_state": {
            "player_state": {
                "inventory_state": {"items": [{"item_id": "ration", "qty": 2}, {"item_id": "water_skin", "qty": 3}]},
                "survival_state": {"hunger": 10, "thirst": 10},
            }
        },
        "runtime_state": {"tick": 0},
    }
    state = _safe_dict(session["simulation_state"])
    discover_location(state, location_id="location:old_mill", reason="phase7_replay_seed", turn_index=0)
    discover_route(state, edge_id="route:old_road:old_mill", reason="phase7_replay_seed", turn_index=0)
    unblock_route(state, edge_id="route:old_road:old_mill", reason="phase7_replay_seed", turn_index=0)
    checkpoint = build_session_checkpoint(session, label="phase7.2:start", turn_index=0)
    commands = [
        {"type": "travel", "command_text": "go to the old road", "roll_encounter": False},
        {"type": "travel", "command_text": "go to the old mill", "roll_encounter": False},
        {"type": "travel", "command_text": "sing a song", "roll_encounter": False},
    ]
    validation = validate_replay_turn_sequence(checkpoint, commands, label="phase7.2")
    contract = build_replay_turn_sequence_contract(validation)
    blockers = []
    first_results = _safe_list(_safe_dict(validation.get("first")).get("command_results"))
    if validation.get("ok") is not True:
        blockers.append({"kind": "replay_sequence_not_validated", "source": SOURCE})
    if [row.get("reason") for row in first_results] != [
        "runtime_travel_command_applied",
        "runtime_travel_command_applied",
        "not_travel_command",
    ]:
        blockers.append({"kind": "unexpected_replay_command_results", "source": SOURCE})
    final_state = _safe_dict(_safe_dict(validation.get("first")).get("final_checkpoint")).get("session", {}).get("simulation_state", {})
    if _safe_dict(final_state).get("travel_state", {}).get("current_location_id") != "location:old_mill":
        blockers.append({"kind": "expected_final_old_mill_location", "source": SOURCE})
    if not contract.get("forbidden_replay_claims"):
        blockers.append({"kind": "missing_replay_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase7_replay_turn_sequence_ready" if not blockers else "phase7_replay_turn_sequence_not_ready",
        "validation": validation,
        "blockers": blockers,
        "source": SOURCE,
    }
