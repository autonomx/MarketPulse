from __future__ import annotations

from typing import Any, Dict, List, cast

from app.apps.rpg.rules.combat.models import (
    AttackIntent,
    AttackResolution,
    DefenseResolution,
    FleeResolution,
)
from app.apps.rpg.rules.combat.rolls import deterministic_d20, deterministic_damage_roll
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _get_actor(simulation_state: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
    for actor in simulation_state.get("actor_states", []) or []:
        if str(actor.get("id") or "") == actor_id:
            return actor
    for npc in simulation_state.get("npc_states", []) or []:
        if str(npc.get("id") or "") == actor_id:
            return npc
    return {}


def _stat(actor: Dict[str, Any], name: str, default: int = 0) -> int:
    stats = _safe_dict(actor.get("stats"))
    return _safe_int(stats.get(name), default)


def _skill(actor: Dict[str, Any], name: str, default: int = 0) -> int:
    skills = _safe_dict(actor.get("skills"))
    return _safe_int(skills.get(name), default)


def _combat_defense_bonus(combat_state: Dict[str, Any], actor_id: str) -> int:
    modifiers = _safe_dict(combat_state.get("defense_modifiers"))
    modifier = _safe_dict(modifiers.get(actor_id))
    return max(0, _safe_int(modifier.get("bonus"), 0))


def _safe_str(value: Any, default: str = "") -> str:
    try:
        return str(value) if value is not None else default
    except Exception:
        return default


def resolve_attack(
    simulation_state: Dict[str, Any],
    combat_state: Dict[str, Any],
    intent: AttackIntent,
    *,
    turn_id: str,
    tick: int,
) -> AttackResolution:
    attacker = _get_actor(simulation_state, intent.actor_id)
    defender = _get_actor(simulation_state, intent.target_id)
    defender_name = str(defender.get("name") or defender.get("id") or intent.target_id or "the target")

    if not attacker or not defender:
        return AttackResolution(
            combat_id=str(combat_state.get("combat_id") or ""),
            actor_id=intent.actor_id,
            target_id=intent.target_id,
            target_name=defender_name,
            action_type=intent.action_type,
            hit=False,
            crit=False,
            attack_total=0,
            defense_total=0,
            damage_total=0,
            damage_type="blunt",
            target_hp_before=0,
            target_hp_after=0,
            target_downed=False,
            rolls=[],
            notes=["invalid_combat_target"],
        )

    attack_roll = deterministic_d20(f"{turn_id}:{tick}:attack:{intent.actor_id}:{intent.target_id}:{intent.action_type}")
    forced_attack_roll = _safe_int(combat_state.get("force_next_attack_roll"), 0)
    if forced_attack_roll > 0:
        attack_roll = dict(attack_roll)
        attack_roll["result"] = max(1, min(20, forced_attack_roll))
        attack_roll["source"] = "manual_force_next_attack_roll"

    damage_roll = deterministic_damage_roll(f"{turn_id}:{tick}:damage:{intent.actor_id}:{intent.target_id}:{intent.action_type}", 4)
    forced_damage = _safe_int(combat_state.get("force_next_damage"), 0)
    if forced_damage > 0:
        damage_roll = dict(damage_roll)
        damage_roll["result"] = forced_damage
        damage_roll["source"] = "manual_force_next_damage"

    strength = _stat(attacker, "strength", 0)
    agility = _stat(attacker, "agility", 0)
    endurance = _stat(defender, "endurance", 0)
    brawling = _skill(attacker, "brawling", 0)
    evasion = _skill(defender, "evasion", 0)

    attack_mod = strength + agility + brawling
    defense_mod = agility + endurance + evasion

    attack_total = cast(int, attack_roll["result"]) + attack_mod
    defense_total = 10 + defense_mod + _combat_defense_bonus(combat_state, intent.target_id)

    hit = attack_total >= defense_total
    crit = attack_roll["result"] == 20

    base_damage = strength + max(0, brawling // 2)
    rolled_damage = cast(int, damage_roll["result"])
    damage_total = 0
    if hit:
        damage_total = max(1, base_damage + rolled_damage - max(0, endurance // 2))
        if crit:
            damage_total += 2

    hp_before = _safe_int(_safe_dict(defender.get("resources")).get("hp"), 1)
    hp_after = max(0, hp_before - damage_total)
    downed = hp_after <= 0

    notes: List[str] = []
    if _combat_defense_bonus(combat_state, intent.target_id) > 0:
        notes.append("target_defending")

    if hit:
        notes.append("attack_hit")
    else:
        notes.append("attack_missed")
    if crit:
        notes.append("critical_hit")
    if downed:
        notes.append("target_downed")

    return AttackResolution(
        combat_id=str(combat_state.get("combat_id") or ""),
        actor_id=intent.actor_id,
        target_id=intent.target_id,
        target_name=defender_name,
        action_type=intent.action_type,
        hit=hit,
        crit=crit,
        attack_total=attack_total,
        defense_total=defense_total,
        damage_total=damage_total,
        damage_type="blunt",
        target_hp_before=hp_before,
        target_hp_after=hp_after,
        target_downed=downed,
        rolls=[attack_roll, damage_roll],
        notes=notes,
    )


def resolve_defend(
    simulation_state: Dict[str, Any],
    combat_state: Dict[str, Any],
    actor_id: str,
) -> DefenseResolution:
    actor = _get_actor(simulation_state, actor_id)
    endurance = _stat(actor, "endurance", 0)
    evasion = _skill(actor, "evasion", 0)
    defense_bonus = max(2, 3 + max(0, endurance // 2) + max(0, evasion // 2))

    return DefenseResolution(
        combat_id=str(combat_state.get("combat_id") or ""),
        actor_id=actor_id,
        action_type="defend",
        defense_bonus=defense_bonus,
        duration="next_incoming_attack",
        notes=["defense_stance"],
    )


def resolve_flee(
    simulation_state: Dict[str, Any],
    combat_state: Dict[str, Any],
    actor_id: str,
    *,
    turn_id: str,
    tick: int,
) -> FleeResolution:
    raw_participants = combat_state.get("participants") or {}
    if isinstance(raw_participants, dict):
        actor = _safe_dict(raw_participants.get(actor_id))
    else:
        actor = _get_actor(simulation_state, actor_id)
    agility = _stat(actor, "agility", 0)
    evasion = _skill(actor, "evasion", 0)

    roll = deterministic_d20(f"{turn_id}:{tick}:flee:{actor_id}")
    flee_total = int(roll.get("result", 0) or 0) + agility + evasion

    raw_participants = combat_state.get("participants") or {}
    if isinstance(raw_participants, dict):
        participant_ids = [str(x) for x in raw_participants.keys() if str(x or "").strip()]
    else:
        participant_ids = [str(x) for x in raw_participants if str(x or "").strip()]
    enemy_count = 0
    highest_enemy_pressure = 0
    actor_team = str(actor.get("combat_team") or actor.get("team") or actor.get("faction") or "party")

    for other_id in participant_ids:
        if other_id == actor_id:
            continue
        if isinstance(raw_participants, dict):
            other = _safe_dict(raw_participants.get(other_id))
        else:
            other = _get_actor(simulation_state, other_id)
        if not other:
            continue
        other_team = str(other.get("combat_team") or other.get("team") or other.get("faction") or "enemy")
        if other_team == actor_team:
            continue
        resources = _safe_dict(other.get("resources"))
        if _safe_int(resources.get("hp"), 0) <= 0:
            continue
        enemy_count += 1
        highest_enemy_pressure = max(
            highest_enemy_pressure,
            _stat(other, "agility", 0) + _skill(other, "awareness", 0),
        )

    difficulty_total = 10 + highest_enemy_pressure + max(0, enemy_count - 1) * 2
    success = flee_total >= difficulty_total
    notes = ["flee_success" if success else "flee_failed"]

    return FleeResolution(
        combat_id=str(combat_state.get("combat_id") or ""),
        actor_id=actor_id,
        action_type="flee",
        success=success,
        flee_total=flee_total,
        difficulty_total=difficulty_total,
        rolls=[roll],
        notes=notes,
    )
