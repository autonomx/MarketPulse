from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.rules.combat.abilities import normalize_ability_cooldowns
from app.apps.rpg.rules.combat.conditions import normalize_status_effects
from app.apps.rpg.rules.combat.positioning import normalize_position
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def build_empty_combat_state() -> Dict[str, Any]:
    return {
        "active": False,
        "combat_id": "",
        "round": 0,
        "phase": "idle",  # idle | initiative | active | resolved
        "participants": [],
        "turn_order": [],
        "initiative": {},
        "turn_index": 0,
        "current_actor_id": "",
        "current_target_id": "",
        "pending_npc_turn": False,
        "defense_modifiers": {},
        "winner_ids": [],
        "loser_ids": [],
        "exit_reason": "",
        "ended_reason": "",
        "last_resolution": {},
        "recent_events": [],
        "tactical_state": {},
    }


def normalize_combat_state(value: Any) -> Dict[str, Any]:
    state = _safe_dict(value)
    if not state:
        return build_empty_combat_state()

    participants = _safe_dict(state.get("participants"))
    normalized_participants: Dict[str, Any] = {}
    for actor_id, participant in participants.items():
        participant = _safe_dict(participant)
        participant["status_effects"] = normalize_status_effects(participant.get("status_effects"))
        participant["ability_cooldowns"] = normalize_ability_cooldowns(participant.get("ability_cooldowns"))
        participant["position"] = normalize_position(participant.get("position"))
        participant["combat_role"] = participant.get("combat_role") or participant.get("role") or ""
        normalized_participants[str(actor_id)] = participant
    participants = normalized_participants

    return {
        "active": bool(state.get("active", False)),
        "combat_id": str(state.get("combat_id") or ""),
        "round": int(state.get("round", 0) or 0),
        "phase": str(state.get("phase") or "idle"),
        "participants": participants,
        "turn_order": [str(x) for x in _safe_list(state.get("turn_order")) if str(x or "").strip()],
        "initiative": {
            str(k): int(v or 0)
            for k, v in _safe_dict(state.get("initiative")).items()
            if str(k or "").strip()
        },
        "turn_index": int(state.get("turn_index", 0) or 0),
        "current_actor_id": str(state.get("current_actor_id") or ""),
        "current_target_id": str(state.get("current_target_id") or ""),
        "pending_npc_turn": bool(state.get("pending_npc_turn", False)),
        "defense_modifiers": _safe_dict(state.get("defense_modifiers")),
        "winner_ids": [str(x) for x in _safe_list(state.get("winner_ids")) if str(x or "").strip()],
        "loser_ids": [str(x) for x in _safe_list(state.get("loser_ids")) if str(x or "").strip()],
        "exit_reason": str(state.get("exit_reason") or ""),
        "ended_reason": str(state.get("ended_reason") or ""),
        "last_resolution": _safe_dict(state.get("last_resolution")),
        "recent_events": _safe_list(state.get("recent_events"))[:24],
        "tactical_state": _safe_dict(state.get("tactical_state")),
        "force_next_attack_roll": _safe_int(state.get("force_next_attack_roll"), 0) if state.get("force_next_attack_roll") else None,
        "force_next_damage": _safe_int(state.get("force_next_damage"), 0) if state.get("force_next_damage") else None,
    }


def combat_is_active(state: Any) -> bool:
    state = normalize_combat_state(state)
    return bool(state.get("active")) and state.get("phase") in {"initiative", "active"}


def get_current_actor_id(state: Any) -> str:
    state = normalize_combat_state(state)
    turn_order = state.get("turn_order") or []
    turn_index = int(state.get("turn_index", 0) or 0)
    if not turn_order:
        return ""
    if turn_index < 0 or turn_index >= len(turn_order):
        turn_index = 0
    return str(turn_order[turn_index] or "")
