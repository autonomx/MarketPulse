from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.combat.conditions import (
    add_status_effect_to_participant,
    build_condition_effect,
    build_condition_result,
    remove_status_effects_from_participant,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


ABILITY_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "ability:power_attack": {
        "ability_id": "ability:power_attack",
        "name": "Power Attack",
        "actor_tags": ["brute"],
        "cooldown_turns": 2,
        "accuracy_modifier": -2,
        "damage_bonus": 3,
        "applies_conditions": [],
        "removes_target_conditions": [],
        "requires_target": True,
    },
    "ability:quick_strike": {
        "ability_id": "ability:quick_strike",
        "name": "Quick Strike",
        "actor_tags": ["quick", "duelist"],
        "cooldown_turns": 1,
        "accuracy_modifier": 1,
        "damage_bonus": 0,
        "applies_conditions": [],
        "removes_target_conditions": [],
        "requires_target": True,
    },
    "ability:shield_bash": {
        "ability_id": "ability:shield_bash",
        "name": "Shield Bash",
        "actor_tags": ["shield", "guard"],
        "cooldown_turns": 2,
        "accuracy_modifier": 0,
        "damage_bonus": 0,
        "applies_conditions": [
            {
                "kind": "stunned",
                "duration_turns": 1,
                "magnitude": 1,
                "stacks": 1,
                "tick_timing": "start_of_turn",
            }
        ],
        "removes_target_conditions": [],
        "requires_target": True,
    },
    "ability:bleeding_slash": {
        "ability_id": "ability:bleeding_slash",
        "name": "Bleeding Slash",
        "actor_tags": ["slasher", "bandit"],
        "cooldown_turns": 2,
        "accuracy_modifier": 0,
        "damage_bonus": 0,
        "applies_conditions": [
            {
                "kind": "bleeding",
                "duration_turns": 3,
                "magnitude": 1,
                "stacks": 1,
                "tick_timing": "start_of_turn",
            }
        ],
        "removes_target_conditions": [],
        "requires_target": True,
    },
    "ability:poison_strike": {
        "ability_id": "ability:poison_strike",
        "name": "Poison Strike",
        "actor_tags": ["poison", "assassin"],
        "cooldown_turns": 3,
        "accuracy_modifier": 0,
        "damage_bonus": -1,
        "applies_conditions": [
            {
                "kind": "poisoned",
                "duration_turns": 3,
                "magnitude": 1,
                "stacks": 1,
                "tick_timing": "start_of_turn",
            }
        ],
        "removes_target_conditions": [],
        "requires_target": True,
    },
    "ability:guard_break": {
        "ability_id": "ability:guard_break",
        "name": "Guard Break",
        "actor_tags": ["brute", "guard_breaker"],
        "cooldown_turns": 2,
        "accuracy_modifier": 0,
        "damage_bonus": 1,
        "applies_conditions": [],
        "removes_target_conditions": ["guarded"],
        "requires_target": True,
    },
}


def get_ability_definition(ability_id: str) -> Dict[str, Any]:
    ability_id = _safe_str(ability_id).strip()
    return deepcopy(_safe_dict(ABILITY_DEFINITIONS.get(ability_id)))


def normalize_ability_cooldowns(value: Any) -> Dict[str, int]:
    cooldowns = _safe_dict(value)
    normalized: Dict[str, int] = {}
    for ability_id, turns in cooldowns.items():
        ability_id = _safe_str(ability_id).strip()
        if not ability_id:
            continue
        normalized[ability_id] = max(0, min(99, _safe_int(turns, 0)))
    return normalized


def get_participant_cooldowns(participant: Dict[str, Any]) -> Dict[str, int]:
    return normalize_ability_cooldowns(_safe_dict(participant).get("ability_cooldowns"))


def decrement_participant_cooldowns(participant: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    participant = dict(_safe_dict(participant))
    cooldowns = get_participant_cooldowns(participant)
    before = dict(cooldowns)
    after: Dict[str, int] = {}

    for ability_id, turns in cooldowns.items():
        next_turns = max(0, _safe_int(turns, 0) - 1)
        if next_turns > 0:
            after[ability_id] = next_turns

    participant["ability_cooldowns"] = after

    return participant, {
        "ticked": before != after,
        "before": before,
        "after": after,
    }


def ability_is_on_cooldown(participant: Dict[str, Any], ability_id: str) -> bool:
    cooldowns = get_participant_cooldowns(participant)
    return _safe_int(cooldowns.get(ability_id), 0) > 0


def set_ability_cooldown(
    participant: Dict[str, Any],
    ability_id: str,
    cooldown_turns: int,
) -> Dict[str, Any]:
    participant = dict(_safe_dict(participant))
    cooldowns = get_participant_cooldowns(participant)
    if cooldown_turns > 0:
        cooldowns[ability_id] = max(1, min(99, cooldown_turns))
    participant["ability_cooldowns"] = cooldowns
    return participant


def _participant_hp(participant: Dict[str, Any]) -> int:
    participant = _safe_dict(participant)
    resources = _safe_dict(participant.get("resources"))
    return _safe_int(participant.get("hp", resources.get("hp")), 0)


def _set_participant_hp(participant: Dict[str, Any], hp: int) -> Dict[str, Any]:
    participant = dict(_safe_dict(participant))
    hp = max(0, hp)
    participant["hp"] = hp
    resources = dict(_safe_dict(participant.get("resources")))
    if resources:
        resources["hp"] = hp
        participant["resources"] = resources
    if hp <= 0:
        participant["status"] = "defeated"
    return participant


def resolve_combat_ability(
    combat_state: Dict[str, Any],
    *,
    actor_id: str,
    target_id: str,
    ability_id: str,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    combat_state = dict(_safe_dict(combat_state))
    participants = dict(_safe_dict(combat_state.get("participants")))

    actor_id = _safe_str(actor_id).strip()
    target_id = _safe_str(target_id).strip()
    ability_id = _safe_str(ability_id).strip()

    ability = get_ability_definition(ability_id)
    actor = dict(_safe_dict(participants.get(actor_id)))
    target = dict(_safe_dict(participants.get(target_id)))

    if not ability:
        return combat_state, {
            "used": False,
            "ability_id": ability_id,
            "reason": "unknown_ability",
            "actor_id": actor_id,
            "target_id": target_id,
        }

    if not actor:
        return combat_state, {
            "used": False,
            "ability_id": ability_id,
            "reason": "actor_not_found",
            "actor_id": actor_id,
            "target_id": target_id,
        }

    if ability.get("requires_target") and not target:
        return combat_state, {
            "used": False,
            "ability_id": ability_id,
            "reason": "target_not_found",
            "actor_id": actor_id,
            "target_id": target_id,
        }

    if ability_is_on_cooldown(actor, ability_id):
        return combat_state, {
            "used": False,
            "ability_id": ability_id,
            "reason": "ability_on_cooldown",
            "actor_id": actor_id,
            "target_id": target_id,
            "cooldown_remaining": get_participant_cooldowns(actor).get(ability_id, 0),
        }

    base_damage = max(1, _safe_int(actor.get("damage_min"), 1))
    damage_bonus = _safe_int(ability.get("damage_bonus"), 0)
    damage_applied = max(0, base_damage + damage_bonus)

    hp_before = _participant_hp(target)
    hp_after = max(0, hp_before - damage_applied)
    target = _set_participant_hp(target, hp_after)

    effects_added: List[Dict[str, Any]] = []
    effects_updated: List[Dict[str, Any]] = []
    effects_removed: List[Dict[str, Any]] = []

    for condition in _safe_list(ability.get("applies_conditions")):
        condition = _safe_dict(condition)
        target, add_result = add_status_effect_to_participant(
            target,
            build_condition_effect(
                kind=_safe_str(condition.get("kind")),
                source_actor_id=actor_id,
                target_actor_id=target_id,
                duration_turns=_safe_int(condition.get("duration_turns"), 1),
                magnitude=_safe_int(condition.get("magnitude"), 1),
                stacks=_safe_int(condition.get("stacks"), 1),
                tick_timing=_safe_str(condition.get("tick_timing") or "start_of_turn"),
            ),
        )
        effects_added.extend(_safe_list(add_result.get("effects_added")))
        effects_updated.extend(_safe_list(add_result.get("effects_updated")))

    remove_kinds = [_safe_str(x) for x in _safe_list(ability.get("removes_target_conditions"))]
    if remove_kinds:
        target, removed = remove_status_effects_from_participant(target, remove_kinds)
        effects_removed.extend(removed)

    cooldown_turns = _safe_int(ability.get("cooldown_turns"), 0)
    actor = set_ability_cooldown(actor, ability_id, cooldown_turns)

    participants[actor_id] = actor
    participants[target_id] = target
    combat_state["participants"] = participants

    condition_result = build_condition_result(
        source="ability",
        target_actor_id=target_id,
        effects_added=effects_added,
        effects_updated=effects_updated,
        effects_removed=effects_removed,
    )

    ability_result = {
        "used": True,
        "ability_id": ability_id,
        "ability_name": _safe_str(ability.get("name")),
        "actor_id": actor_id,
        "target_id": target_id,
        "target_name": _safe_str(target.get("name") or target_id),
        "damage_applied": damage_applied,
        "damage_bonus": damage_bonus,
        "target_hp_before": hp_before,
        "target_hp_after": hp_after,
        "defeated": hp_after <= 0,
        "cooldown_turns": cooldown_turns,
        "condition_result": condition_result,
        "reason": "ability_used",
    }

    combat_state["last_ability_result"] = ability_result
    recent = list(combat_state.get("recent_events") or [])
    recent.append({
        "type": "ability_used",
        "actor_id": actor_id,
        "target_id": target_id,
        "ability_id": ability_id,
        "damage_applied": damage_applied,
    })
    combat_state["recent_events"] = recent[-24:]

    return combat_state, ability_result