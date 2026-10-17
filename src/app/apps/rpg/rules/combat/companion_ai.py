from __future__ import annotations

from typing import Any, Dict, List, cast

from app.apps.rpg.rules.combat.abilities import resolve_combat_ability
from app.apps.rpg.rules.combat.apply import apply_defense_resolution
from app.apps.rpg.rules.combat.resolver import resolve_defend
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _hp(participant: Dict[str, Any]) -> int:
    resources = _safe_dict(_safe_dict(participant).get("resources"))
    return _safe_int(_safe_dict(participant).get("hp", resources.get("hp")), 0)


def _max_hp(participant: Dict[str, Any]) -> int:
    resources = _safe_dict(_safe_dict(participant).get("resources"))
    return max(1, _safe_int(_safe_dict(participant).get("max_hp", resources.get("max_hp")), 1))


def _hp_ratio(participant: Dict[str, Any]) -> float:
    return max(0.0, min(1.0, _hp(participant) / float(_max_hp(participant))))


def _effect_kinds(participant: Dict[str, Any]) -> set[str]:
    kinds: set[str] = set()
    for effect in _safe_list(_safe_dict(participant).get("status_effects")):
        kind = _safe_str(_safe_dict(effect).get("kind")).strip().lower()
        if kind:
            kinds.add(kind)
    return kinds


def is_companion_active(participant: Dict[str, Any]) -> bool:
    participant = _safe_dict(participant)
    if _hp(participant) <= 0:
        return False
    status = _safe_str(participant.get("status")).strip().lower()
    if status in {"downed", "unconscious", "defeated", "dead", "fled"}:
        return False
    effects = _effect_kinds(participant)
    if "downed" in effects or "unconscious" in effects or "stunned" in effects:
        return False
    return True


def _enemy_candidates(combat_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for actor_id, participant in _safe_dict(_safe_dict(combat_state).get("participants")).items():
        participant = _safe_dict(participant)
        if _safe_str(participant.get("side")).strip().lower() != "enemy":
            continue
        if _hp(participant) <= 0:
            continue
        if _safe_str(participant.get("status")).strip().lower() in {"defeated", "dead", "fled"}:
            continue
        out.append({
            "actor_id": str(actor_id),
            "hp": _hp(participant),
            "hp_ratio": _hp_ratio(participant),
        })
    out.sort(key=lambda row: (float(cast(Any, row.get("hp_ratio", 1.0))), int(cast(Any, row.get("hp", 999999))), str(row.get("actor_id"))))
    return out


def _lowest_hp_party_member(combat_state: Dict[str, Any]) -> Dict[str, Any]:
    candidates = []
    for actor_id, participant in _safe_dict(_safe_dict(combat_state).get("participants")).items():
        participant = _safe_dict(participant)
        side = _safe_str(
            participant.get("side")
            or participant.get("team")
            or participant.get("combat_team")
        ).strip().lower()
        if str(actor_id) == "player":
            side = "party"
        if side != "party":
            continue
        if _hp(participant) <= 0:
            continue
        candidates.append({
            "actor_id": str(actor_id),
            "hp_ratio": _hp_ratio(participant),
            "hp": _hp(participant),
        })
    candidates.sort(key=lambda row: (float(cast(Any, row.get("hp_ratio", 1.0))), int(cast(Any, row.get("hp", 999999))), str(row.get("actor_id"))))
    return candidates[0] if candidates else {}


def parse_companion_command(player_input: str) -> Dict[str, Any]:
    text = _safe_str(player_input).strip().lower()
    if "bran" not in text:
        return {}

    command = ""
    if "attack" in text:
        command = "attack"
    elif "defend me" in text or "protect me" in text:
        command = "defend_player"
    elif "defend" in text:
        command = "defend"
    elif "potion" in text or "heal" in text:
        command = "heal"
    elif "fall back" in text or "fallback" in text:
        command = "fall_back"
    elif "dance" in text or "sing" in text or "juggle" in text or "taunt me" in text:
        command = "invalid"

    # If the player directly addresses Bran in combat but the verb is not a
    # supported command, treat it as an invalid companion command instead of
    # letting social/no-op routing consume it.
    if not command and ("bran," in text or text.startswith("bran ")):
        command = "invalid"

    if not command:
        return {}

    return {
        "detected": True,
        "companion_actor_id": "npc:bran",
        "command": command,
        "raw_text": player_input,
    }


def choose_companion_intent(
    combat_state: Dict[str, Any],
    actor_id: str,
    *,
    command: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    combat_state = _safe_dict(combat_state)
    participants = _safe_dict(combat_state.get("participants"))
    actor = _safe_dict(participants.get(actor_id))

    if not actor:
        return {
            "selected": False,
            "actor_id": actor_id,
            "intent": "",
            "reason": "companion_not_found",
        }

    if not is_companion_active(actor):
        return {
            "selected": True,
            "actor_id": actor_id,
            "intent": "skip_turn",
            "reason": "companion_inactive",
        }

    role = _safe_str(actor.get("combat_role") or actor.get("role") or "striker").strip().lower()

    if command and command.get("detected"):
        cmd = _safe_str(command.get("command")).strip()
        if cmd == "invalid":
            return {
                "selected": False,
                "actor_id": actor_id,
                "intent": "",
                "reason": "invalid_command",
                "companion_command_result": {
                    "accepted": False,
                    "command": cmd,
                    "reason": "unsupported_companion_command",
                },
            }

        if cmd == "attack":
            target = (_enemy_candidates(combat_state) or [{}])[0]
            return {
                "selected": bool(target),
                "actor_id": actor_id,
                "role": role,
                "intent": "attack",
                "target_actor_id": target.get("actor_id", ""),
                "reason": "commanded_attack",
                "companion_command_result": {
                    "accepted": bool(target),
                    "command": cmd,
                    "target_actor_id": target.get("actor_id", ""),
                },
            }

        if cmd in {"defend_player", "defend"}:
            return {
                "selected": True,
                "actor_id": actor_id,
                "role": role,
                "intent": "defend",
                "target_actor_id": "player",
                "reason": "commanded_defend",
                "companion_command_result": {
                    "accepted": True,
                    "command": cmd,
                    "target_actor_id": "player",
                },
            }

        if cmd == "fall_back":
            return {
                "selected": True,
                "actor_id": actor_id,
                "role": role,
                "intent": "fall_back",
                "reason": "commanded_fall_back",
                "companion_command_result": {
                    "accepted": True,
                    "command": cmd,
                },
            }

    if role == "protector":
        player = _safe_dict(_safe_dict(combat_state.get("participants")).get("player"))
        if player and _hp(player) > 0 and _hp_ratio(player) <= 0.5:
            return {
                "selected": True,
                "actor_id": actor_id,
                "role": role,
                "intent": "defend",
                "target_actor_id": "player",
                "reason": "protect_low_hp_player",
                "companion_command_result": {},
            }

        low = _lowest_hp_party_member(combat_state)
        if low and low.get("actor_id") == "player" and float(low.get("hp_ratio", 1.0)) <= 0.5:
            return {
                "selected": True,
                "actor_id": actor_id,
                "role": role,
                "intent": "defend",
                "target_actor_id": "player",
                "reason": "protect_low_hp_player",
                "companion_command_result": {},
            }

    target = (_enemy_candidates(combat_state) or [{}])[0]
    if target:
        return {
            "selected": True,
            "actor_id": actor_id,
            "role": role,
            "intent": "attack",
            "target_actor_id": target.get("actor_id", ""),
            "reason": "role_striker_lowest_hp_enemy",
            "companion_command_result": {},
        }

    return {
        "selected": True,
        "actor_id": actor_id,
        "role": role,
        "intent": "hold",
        "reason": "no_enemy_targets",
        "companion_command_result": {},
    }


def apply_companion_intent(
    simulation_state: Dict[str, Any],
    combat_state: Dict[str, Any],
    intent_result: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    combat_state = dict(_safe_dict(combat_state))
    actor_id = _safe_str(intent_result.get("actor_id")).strip()
    intent = _safe_str(intent_result.get("intent")).strip()
    target_id = _safe_str(intent_result.get("target_actor_id")).strip()

    if intent == "skip_turn":
        return simulation_state, combat_state, {
            "action_type": "skip_turn",
            "actor_id": actor_id,
            "reason": intent_result.get("reason", "companion_inactive"),
            "companion_intent_result": intent_result,
            "companion_command_result": intent_result.get("companion_command_result", {}),
        }

    if intent == "defend":
        defense = resolve_defend(simulation_state, combat_state, actor_id).to_dict()
        simulation_state, combat_state = apply_defense_resolution(simulation_state, combat_state, defense)
        return simulation_state, combat_state, {
            "action_type": "defend",
            "actor_id": actor_id,
            "target_id": target_id,
            "reason": intent_result.get("reason", "companion_defend"),
            "combat_result": defense,
            "companion_intent_result": intent_result,
            "companion_command_result": intent_result.get("companion_command_result", {}),
        }

    if intent == "fall_back":
        participants = dict(_safe_dict(combat_state.get("participants")))
        actor = dict(_safe_dict(participants.get(actor_id)))
        position = dict(_safe_dict(actor.get("position")))
        position["zone"] = "backline"
        position["range_band"] = "far"
        position["engaged_with"] = []
        actor["position"] = position
        participants[actor_id] = actor
        combat_state["participants"] = participants
        return simulation_state, combat_state, {
            "action_type": "fall_back",
            "actor_id": actor_id,
            "reason": "companion_fell_back",
            "companion_intent_result": intent_result,
            "companion_command_result": intent_result.get("companion_command_result", {}),
            "position_result": {
                "changed": True,
                "actor_id": actor_id,
                "to": position,
                "reason": "fall_back",
            },
        }

    if intent == "attack":
        # v1 companion attack uses a deterministic light strike via ability-style result.
        combat_state, ability_result = resolve_combat_ability(
            combat_state,
            actor_id=actor_id,
            target_id=target_id,
            ability_id="ability:quick_strike",
        )
        return simulation_state, combat_state, {
            "action_type": "attack",
            "actor_id": actor_id,
            "target_id": target_id,
            "reason": intent_result.get("reason", "companion_attack"),
            "ability_result": ability_result,
            "combat_result": {
                "action_type": "attack",
                "ability_result": ability_result,
                "target_id": target_id,
                "damage_applied": ability_result.get("damage_applied", 0),
            },
            "companion_intent_result": intent_result,
            "companion_command_result": intent_result.get("companion_command_result", {}),
        }

    return simulation_state, combat_state, {
        "action_type": "hold",
        "actor_id": actor_id,
        "reason": intent_result.get("reason", "companion_hold"),
        "companion_intent_result": intent_result,
        "companion_command_result": intent_result.get("companion_command_result", {}),
    }