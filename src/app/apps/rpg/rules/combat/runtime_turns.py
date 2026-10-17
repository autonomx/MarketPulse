from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.rules.combat.conditions import (
    actor_has_condition,
    remove_status_effects_from_participant,
    tick_start_of_turn_status_effects,
)
from app.apps.rpg.rules.combat.runtime_core import (
    SOURCE,
    current_actor_id,
    get_combat_state,
    safe_dict,
    safe_int,
    safe_list,
    safe_str,
)


def validate_combat_turn(
    simulation_state: Dict[str, Any],
    *,
    actor_id: str,
) -> Dict[str, Any]:
    combat_state = get_combat_state(simulation_state)
    if combat_state.get("active") is not True:
        return {"allowed": True, "reason": "combat_not_active", "source": SOURCE}

    actor_id = safe_str(actor_id or "player")
    current_actor = current_actor_id(combat_state)
    if actor_id != current_actor:
        return {
            "allowed": False,
            "reason": "not_actor_turn",
            "requested_actor_id": actor_id,
            "current_actor_id": current_actor,
            "combat_state": deepcopy(combat_state),
            "source": SOURCE,
        }

    return {
        "allowed": True,
        "reason": "actor_turn_allowed",
        "requested_actor_id": actor_id,
        "current_actor_id": current_actor,
        "combat_state": deepcopy(combat_state),
        "source": SOURCE,
    }


def advance_combat_turn(
    simulation_state: Dict[str, Any],
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    combat_state = get_combat_state(simulation_state)
    if combat_state.get("active") is not True:
        return {"resolved": False, "changed_state": False, "reason": "combat_not_active", "source": SOURCE}

    order = safe_list(combat_state.get("initiative_order"))
    if not order:
        return {
            "resolved": False,
            "changed_state": False,
            "reason": "initiative_order_missing",
            "combat_state": deepcopy(combat_state),
            "source": SOURCE,
        }

    previous_actor = current_actor_id(combat_state)
    next_index = safe_int(combat_state.get("turn_index"), 0) + 1
    round_num = safe_int(combat_state.get("round"), 1)
    if next_index >= len(order):
        next_index = 0
        round_num += 1

    combat_state["turn_index"] = next_index
    combat_state["round"] = round_num
    combat_state["current_actor_id"] = current_actor_id(combat_state)
    _apply_start_of_turn_conditions(combat_state)

    combat_state.setdefault("combat_log", []).append({
        "kind": "turn_advanced",
        "previous_actor_id": previous_actor,
        "current_actor_id": combat_state["current_actor_id"],
        "round": round_num,
        "turn_index": next_index,
        "tick": int(tick or 0),
    })
    simulation_state["combat_state"] = combat_state

    return {
        "resolved": True,
        "changed_state": True,
        "reason": "combat_turn_advanced",
        "previous_actor_id": previous_actor,
        "current_actor_id": combat_state["current_actor_id"],
        "round": round_num,
        "turn_index": next_index,
        "combat_state": deepcopy(combat_state),
        "tick": int(tick or 0),
        "source": SOURCE,
    }


def gate_combat_action(
    simulation_state: Dict[str, Any],
    *,
    actor_id: str,
    action_kind: str,
) -> Dict[str, Any]:
    turn = validate_combat_turn(simulation_state, actor_id=actor_id)
    if turn.get("allowed") is not True:
        return _gate_response(False, turn, actor_id, action_kind, safe_str(turn.get("reason") or "not_actor_turn"))

    allowed_actions = {"attack", "defend", "wait", "flee", "use", "consume", "equip"}
    if safe_str(action_kind) not in allowed_actions:
        response = _gate_response(False, turn, actor_id, action_kind, "combat_action_not_allowed")
        response["allowed_actions"] = sorted(allowed_actions)
        return response

    return _gate_response(True, turn, actor_id, action_kind, "combat_action_allowed")


def _apply_start_of_turn_conditions(combat_state: Dict[str, Any]) -> None:
    participants = safe_dict(combat_state.get("participants"))
    current_actor = safe_str(combat_state.get("current_actor_id")).strip()
    current_participant = safe_dict(participants.get(current_actor))
    if not current_participant:
        return

    was_stunned = actor_has_condition(current_participant, "stunned")
    current_participant, tick_result = tick_start_of_turn_status_effects(current_participant)
    participants[current_actor] = current_participant
    combat_state["participants"] = participants
    if tick_result.get("ticked"):
        combat_state["last_condition_tick_result"] = {"actor_id": current_actor, **tick_result}
        recent = list(combat_state.get("recent_events") or [])
        recent.append({"type": "condition_tick", "actor_id": current_actor, "tick_result": tick_result})
        combat_state["recent_events"] = recent[-24:]

    if not was_stunned:
        return

    current_participant, removed = remove_status_effects_from_participant(current_participant, ["stunned"])
    participants[current_actor] = current_participant
    combat_state["participants"] = participants
    combat_state["last_condition_result"] = {
        "applied": True,
        "source": "combat",
        "target_actor_id": current_actor,
        "effects_added": [],
        "effects_updated": [],
        "effects_removed": removed,
        "reason": "stunned_skip_turn",
    }
    combat_state["pending_skip_turn_actor_id"] = current_actor


def _gate_response(
    resolved: bool,
    turn: Dict[str, Any],
    actor_id: str,
    action_kind: str,
    reason: str,
) -> Dict[str, Any]:
    return {
        "resolved": resolved,
        "changed_state": False,
        "reason": reason,
        "requested_actor_id": safe_str(actor_id or "player"),
        "current_actor_id": safe_str(turn.get("current_actor_id")),
        "action_kind": safe_str(action_kind),
        "combat_state": deepcopy(safe_dict(turn.get("combat_state"))),
        "source": SOURCE,
    }
