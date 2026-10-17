from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.rules.combat.conditions import (
    add_status_effect_to_participant,
    build_condition_effect,
    build_condition_result,
)
from app.apps.rpg.rules.combat.state import normalize_combat_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def apply_attack_resolution(
    simulation_state: Dict[str, Any],
    combat_state: Dict[str, Any],
    resolution: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    combat_state = normalize_combat_state(combat_state)
    resolution = dict(resolution)
    target_id = str(resolution.get("target_id") or "")
    actor_id = str(resolution.get("actor_id") or "")
    hp_after = int(resolution.get("target_hp_after", 0) or 0)

    for collection_key in ("actor_states", "npc_states"):
        collection = simulation_state.get(collection_key) or []
        for actor in collection:
            if str(actor.get("id") or "") != target_id:
                continue
            resources = _safe_dict(actor.get("resources"))
            resources["hp"] = hp_after
            actor["resources"] = resources
            if hp_after <= 0:
                statuses = actor.get("status_effects") or []
                if "downed" not in statuses:
                    statuses.append("downed")
                actor["status_effects"] = statuses

    combat_state["active"] = True
    combat_state["phase"] = "active"
    combat_state["last_resolution"] = dict(resolution)

    condition_result: Dict[str, Any] = {}
    participants = _safe_dict(combat_state.get("participants"))
    target_participant = _safe_dict(participants.get(target_id))

    if target_participant and bool(resolution.get("hit")) and _safe_int(resolution.get("damage_applied"), 0) > 0:
        effects_added: List[Dict[str, Any]] = []
        effects_updated: List[Dict[str, Any]] = []

        attack_roll = _safe_int(resolution.get("attack_roll"), 0)
        damage_applied = _safe_int(resolution.get("damage_applied"), 0)
        target_max_hp = _safe_int(
            target_participant.get("max_hp")
            or _safe_dict(target_participant.get("resources")).get("max_hp"),
            0,
        )

        if attack_roll >= 20:
            target_participant, added_result = add_status_effect_to_participant(
                target_participant,
                build_condition_effect(
                    kind="bleeding",
                    source_actor_id=actor_id,
                    target_actor_id=target_id,
                    duration_turns=3,
                    magnitude=1,
                    stacks=1,
                ),
            )
            effects_added.extend(_safe_list(added_result.get("effects_added")))
            effects_updated.extend(_safe_list(added_result.get("effects_updated")))

        if target_max_hp > 0 and damage_applied * 2 >= target_max_hp:
            target_participant, added_result = add_status_effect_to_participant(
                target_participant,
                build_condition_effect(
                    kind="stunned",
                    source_actor_id=actor_id,
                    target_actor_id=target_id,
                    duration_turns=1,
                    magnitude=1,
                    stacks=1,
                    tick_timing="start_of_turn",
                ),
            )
            effects_added.extend(_safe_list(added_result.get("effects_added")))
            effects_updated.extend(_safe_list(added_result.get("effects_updated")))

        participants[target_id] = target_participant
        combat_state["participants"] = participants

        if effects_added or effects_updated:
            condition_result = build_condition_result(
                source="combat",
                target_actor_id=target_id,
                effects_added=effects_added,
                effects_updated=effects_updated,
            )
            resolution["condition_result"] = condition_result
            combat_state["last_condition_result"] = condition_result

    defense_modifiers = _safe_dict(combat_state.get("defense_modifiers"))
    if target_id in defense_modifiers:
        defense_modifiers.pop(target_id, None)
    combat_state["defense_modifiers"] = defense_modifiers

    recent = list(combat_state.get("recent_events") or [])
    recent.append({
        "type": "attack_resolution",
        "actor_id": resolution.get("actor_id"),
        "target_id": resolution.get("target_id"),
        "hit": bool(resolution.get("hit")),
        "damage_total": int(resolution.get("damage_total", 0) or 0),
        "target_downed": bool(resolution.get("target_downed")),
    })
    combat_state["recent_events"] = recent[-24:]

    if bool(resolution.get("target_downed")):
        combat_state["current_target_id"] = ""

    combat_state.pop("force_next_attack_roll", None)
    combat_state.pop("force_next_damage", None)

    return simulation_state, combat_state


def apply_defense_resolution(
    simulation_state: Dict[str, Any],
    combat_state: Dict[str, Any],
    resolution: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    state = normalize_combat_state(combat_state)
    actor_id = str(resolution.get("actor_id") or "")
    bonus = int(resolution.get("defense_bonus", 0) or 0)

    modifiers = _safe_dict(state.get("defense_modifiers"))
    if actor_id and bonus > 0:
        modifiers[actor_id] = {
            "bonus": bonus,
            "duration": "next_incoming_attack",
        }
    state["defense_modifiers"] = modifiers

    recent = list(state.get("recent_events") or [])
    recent.append({
        "type": "defense_resolution",
        "actor_id": actor_id,
        "defense_bonus": bonus,
    })
    state["recent_events"] = recent[-24:]
    state["last_resolution"] = dict(resolution)
    return simulation_state, state


def apply_flee_resolution(
    simulation_state: Dict[str, Any],
    combat_state: Dict[str, Any],
    resolution: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    state = normalize_combat_state(combat_state)
    actor_id = str(resolution.get("actor_id") or "")
    success = bool(resolution.get("success"))

    recent = list(state.get("recent_events") or [])
    recent.append({
        "type": "flee_resolution",
        "actor_id": actor_id,
        "success": success,
    })
    state["recent_events"] = recent[-24:]
    state["last_resolution"] = dict(resolution)

    if success:
        state["active"] = False
        state["phase"] = "resolved"
        state["exit_reason"] = "fled"
        state["winner_ids"] = []
        state["loser_ids"] = []
        state["pending_npc_turn"] = False

    return simulation_state, state
