from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.rules.combat.runtime_core import (
    SOURCE,
    combat_seed,
    default_target_for_actor,
    deterministic_roll,
    damage_bounds_for_actor,
    get_combat_state,
    living_enemy_ids,
    living_party_ids,
    participant,
    safe_dict,
    safe_int,
    safe_list,
    safe_str,
    sync_participant_hp_to_actor_state,
)
from app.apps.rpg.rules.combat.runtime_turns import advance_combat_turn, gate_combat_action
from app.apps.rpg.rules.interactions.equipment_runtime import (
    consume_equipped_ammo,
    project_equipment_stats,
)
from app.apps.rpg.rules.interactions.loot_runtime import generate_loot_from_table


def resolve_combat_attack(
    simulation_state: Dict[str, Any],
    *,
    actor_id: str = "player",
    target_id: str = "",
    session_id: str = "",
    tick: int = 0,
    combat_modifiers: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    combat_state = get_combat_state(simulation_state)
    if combat_state.get("active") is not True:
        return {"resolved": False, "changed_state": False, "reason": "combat_not_active", "source": SOURCE}

    actor_id = safe_str(actor_id or "player")
    combat_modifiers = safe_dict(combat_modifiers)
    gate = gate_combat_action(simulation_state, actor_id=actor_id, action_kind="attack")
    if gate.get("resolved") is not True:
        return gate

    participants = safe_dict(combat_state.get("participants"))
    actor = participant(combat_state, actor_id)
    if not actor:
        return _missing_actor_response(combat_state, actor_id)

    target_id = safe_str(target_id) or default_target_for_actor(combat_state, actor_id)
    target = participant(combat_state, target_id)
    if not target:
        return _missing_target_response(combat_state, actor_id, target_id)
    if safe_str(target.get("status") or "active") != "active" or safe_int(target.get("hp"), 0) <= 0:
        return _inactive_target_response(combat_state, actor_id, target_id)

    ammo_result = _consume_required_ammo(simulation_state, actor_id, target_id, combat_state, tick)
    if ammo_result.get("blocked"):
        return ammo_result["response"]

    resolution = _roll_attack(
        simulation_state,
        combat_state,
        actor_id,
        target_id,
        target,
        combat_modifiers,
    )
    target["hp"] = resolution["target_hp_after"]
    target["status"] = "defeated" if resolution["defeated"] else safe_str(target.get("status") or "active")
    participants[target_id] = target
    combat_state["participants"] = participants
    sync_participant_hp_to_actor_state(
        simulation_state,
        actor_id=target_id,
        hp=resolution["target_hp_after"],
        status=safe_str(target.get("status")),
    )

    combat_log_entry = _combat_log_entry(combat_state, actor_id, target_id, resolution, tick)
    combat_state.setdefault("combat_log", []).append(combat_log_entry)
    combat_state, combat_ended, loot_result, next_actor_id = _finish_attack(
        simulation_state,
        combat_state,
        target,
        target_id,
        session_id,
        tick,
        resolution["defeated"],
    )
    xp_result = safe_dict(loot_result.get("xp_result"))

    return {
        "resolved": True,
        "changed_state": True,
        "reason": "combat_defeat_resolved" if resolution["defeated"] else "combat_attack_resolved",
        "actor_id": actor_id,
        "target_id": target_id,
        **resolution,
        "combat_ended": combat_ended,
        "next_actor_id": next_actor_id,
        "ammo_result": deepcopy(safe_dict(ammo_result.get("result"))),
        "loot_result": deepcopy(loot_result),
        "xp_result": deepcopy(xp_result),
        "combat_log_entry": deepcopy(combat_log_entry),
        "combat_state": deepcopy(combat_state),
        "tick": int(tick or 0),
        "source": SOURCE,
    }


def _roll_attack(
    simulation_state: Dict[str, Any],
    combat_state: Dict[str, Any],
    actor_id: str,
    target_id: str,
    target: Dict[str, Any],
    combat_modifiers: Dict[str, Any],
) -> Dict[str, Any]:
    actor_stats = damage_bounds_for_actor(simulation_state, actor_id)
    morale_accuracy_bonus = safe_int(combat_modifiers.get("accuracy_bonus"), 0)
    morale_damage_bonus = safe_int(combat_modifiers.get("damage_bonus"), 0)
    target_defense = safe_int(target.get("defense"), 10)
    target_armor = safe_int(target.get("armor"), 0)
    attack_roll = deterministic_roll(combat_seed(combat_state, "attack", actor_id, target_id), 1, 20)
    attack_total = (
        attack_roll
        + actor_stats["accuracy_bonus"]
        + morale_accuracy_bonus
        - actor_stats["encumbrance_penalty"]
    )
    hit = attack_total >= target_defense
    hp_before = safe_int(target.get("hp"), 0)
    damage_roll = 0
    armor_reduction = 0
    damage_applied = 0
    hp_after = hp_before

    if hit:
        damage_roll = deterministic_roll(
            combat_seed(combat_state, "damage", actor_id, target_id),
            actor_stats["damage_min"],
            actor_stats["damage_max"],
        )
        armor_reduction = max(0, target_armor)
        damage_applied = max(1, damage_roll + morale_damage_bonus - armor_reduction)
        hp_after = max(0, hp_before - damage_applied)

    return {
        "hit": hit,
        "attack_roll": attack_roll,
        "attack_total": attack_total,
        "equipment_accuracy_bonus": actor_stats["accuracy_bonus"],
        "morale_accuracy_bonus": morale_accuracy_bonus,
        "target_defense": target_defense,
        "damage_roll": damage_roll,
        "morale_damage_bonus": morale_damage_bonus,
        "armor_reduction": armor_reduction,
        "damage_applied": damage_applied,
        "target_hp_before": hp_before,
        "target_hp_after": hp_after,
        "defeated": hp_after <= 0,
    }


def _consume_required_ammo(
    simulation_state: Dict[str, Any],
    actor_id: str,
    target_id: str,
    combat_state: Dict[str, Any],
    tick: int,
) -> Dict[str, Any]:
    equipment_stats = project_equipment_stats(simulation_state, actor_id=actor_id)
    requires_ammo = any(
        safe_str(safe_dict(equipped).get("slot")) == "main_hand"
        for equipped in safe_list(equipment_stats.get("equipped_items"))
    ) and safe_int(safe_dict(equipment_stats.get("stats")).get("range"), 1) > 1
    if not requires_ammo:
        return {"blocked": False, "result": {}}

    ammo_result = consume_equipped_ammo(simulation_state, actor_id=actor_id, quantity=1, tick=tick)
    if ammo_result.get("consumed") is True or actor_id != "player":
        return {"blocked": False, "result": ammo_result}

    return {
        "blocked": True,
        "response": {
            "resolved": False,
            "changed_state": False,
            "reason": "combat_ammo_required",
            "actor_id": actor_id,
            "target_id": target_id,
            "ammo_result": deepcopy(ammo_result),
            "combat_state": deepcopy(combat_state),
            "source": SOURCE,
        },
    }


def _finish_attack(
    simulation_state: Dict[str, Any],
    combat_state: Dict[str, Any],
    target: Dict[str, Any],
    target_id: str,
    session_id: str,
    tick: int,
    defeated: bool,
) -> tuple[Dict[str, Any], bool, Dict[str, Any], str]:
    if defeated and not living_enemy_ids(combat_state):
        combat_state["active"] = False
        combat_state["ended_reason"] = "enemy_side_defeated"
        loot_result = generate_loot_from_table(
            simulation_state,
            loot_table_id=safe_str(target.get("loot_table_id") or "loot:bandit_common"),
            source_id=target_id,
            session_id=session_id,
            tick=tick,
            add_to_inventory=True,
        )
        simulation_state["combat_state"] = combat_state
        return combat_state, True, safe_dict(loot_result), ""

    if defeated and not living_party_ids(combat_state):
        combat_state["active"] = False
        combat_state["ended_reason"] = "party_side_defeated"
        simulation_state["combat_state"] = combat_state
        return combat_state, True, {}, ""

    advance = advance_combat_turn(simulation_state, tick=tick)
    combat_state = safe_dict(simulation_state.get("combat_state"))
    return combat_state, False, {}, safe_str(advance.get("current_actor_id"))


def _combat_log_entry(
    combat_state: Dict[str, Any],
    actor_id: str,
    target_id: str,
    resolution: Dict[str, Any],
    tick: int,
) -> Dict[str, Any]:
    return {
        "kind": "attack",
        "round": safe_int(combat_state.get("round"), 1),
        "turn_index": safe_int(combat_state.get("turn_index"), 0),
        "actor_id": actor_id,
        "target_id": target_id,
        **resolution,
        "tick": int(tick or 0),
    }


def _missing_actor_response(combat_state: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
    return {
        "resolved": False,
        "changed_state": False,
        "reason": "combat_actor_not_found",
        "actor_id": actor_id,
        "combat_state": deepcopy(combat_state),
        "source": SOURCE,
    }


def _missing_target_response(combat_state: Dict[str, Any], actor_id: str, target_id: str) -> Dict[str, Any]:
    return {
        "resolved": False,
        "changed_state": False,
        "reason": "combat_target_not_found",
        "actor_id": actor_id,
        "target_id": target_id,
        "combat_state": deepcopy(combat_state),
        "source": SOURCE,
    }


def _inactive_target_response(combat_state: Dict[str, Any], actor_id: str, target_id: str) -> Dict[str, Any]:
    return {
        "resolved": False,
        "changed_state": False,
        "reason": "combat_target_not_active",
        "actor_id": actor_id,
        "target_id": target_id,
        "combat_state": deepcopy(combat_state),
        "source": SOURCE,
    }
