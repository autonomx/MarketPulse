from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.rules.combat.conditions import (
    add_status_effect_to_participant,
    build_condition_effect,
    remove_status_effects_from_participant,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def stabilize_participant(
    combat_state: Dict[str, Any],
    target_actor_id: str,
    *,
    source_actor_id: str = "player",
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    combat_state = dict(_safe_dict(combat_state))
    participants = dict(_safe_dict(combat_state.get("participants")))
    target_actor_id = _safe_str(target_actor_id).strip()
    participant = dict(_safe_dict(participants.get(target_actor_id)))

    if not participant:
        return combat_state, {
            "applied": False,
            "reason": "target_not_found",
            "target_actor_id": target_actor_id,
        }

    hp = _safe_int(participant.get("hp"), 0)
    if hp > 0:
        return combat_state, {
            "applied": False,
            "reason": "target_not_downed",
            "target_actor_id": target_actor_id,
        }

    participant, add_result = add_status_effect_to_participant(
        participant,
        build_condition_effect(
            kind="stabilized",
            source_actor_id=source_actor_id,
            target_actor_id=target_actor_id,
            duration_turns=999,
            magnitude=1,
            stacks=1,
            tick_timing="none",
        ),
    )

    participants[target_actor_id] = participant
    combat_state["participants"] = participants

    result = {
        "applied": True,
        "source": "combat",
        "action_type": "stabilize",
        "target_actor_id": target_actor_id,
        "effects_added": add_result.get("effects_added", []),
        "effects_updated": add_result.get("effects_updated", []),
        "effects_removed": [],
        "reason": "stabilized",
    }
    combat_state["last_recovery_result"] = result
    return combat_state, result


def revive_participant_with_healing(
    combat_state: Dict[str, Any],
    target_actor_id: str,
    *,
    amount: int,
    source_actor_id: str = "player",
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    combat_state = dict(_safe_dict(combat_state))
    participants = dict(_safe_dict(combat_state.get("participants")))
    target_actor_id = _safe_str(target_actor_id).strip()
    participant = dict(_safe_dict(participants.get(target_actor_id)))

    if not participant:
        return combat_state, {
            "applied": False,
            "reason": "target_not_found",
            "target_actor_id": target_actor_id,
        }

    hp_before = _safe_int(participant.get("hp"), 0)
    max_hp = max(1, _safe_int(participant.get("max_hp"), 1))
    hp_after = min(max_hp, max(0, hp_before) + max(0, amount))
    participant["hp"] = hp_after

    removed: list[Any] = []
    if hp_after > 0:
        participant, removed = remove_status_effects_from_participant(
            participant,
            ["downed", "unconscious"],
        )
        participant["status"] = "active"

    participants[target_actor_id] = participant
    combat_state["participants"] = participants

    result = {
        "applied": hp_after > hp_before,
        "source": "combat",
        "action_type": "revive",
        "target_actor_id": target_actor_id,
        "source_actor_id": source_actor_id,
        "hp_before": hp_before,
        "hp_after": hp_after,
        "effects_removed": removed,
        "reason": "revived" if hp_after > 0 else "not_revived",
    }
    combat_state["last_recovery_result"] = result
    return combat_state, result