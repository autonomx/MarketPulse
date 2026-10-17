"""Phase 18.3A — Deterministic Action Resolution Engine.

Provides authoritative game-engine logic for resolving player/NPC actions.
The engine decides outcomes; the LLM layer merely narrates.
No randomness without explicit seed.
"""
from __future__ import annotations

import random
from typing import Any, Dict, cast
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict

# ---------------------------------------------------------------------------
# Action Profiles
# ---------------------------------------------------------------------------

_ACTION_PROFILES: Dict[str, Dict[str, str]] = {
    "attack_unarmed": {"stat": "strength", "skill": ""},
    "attack_melee": {"stat": "strength", "skill": "swordsmanship"},
    "attack_ranged": {"stat": "dexterity", "skill": "archery"},
    "observe": {"stat": "", "skill": ""},
    "block": {"stat": "strength", "skill": "defense"},
    "dodge": {"stat": "dexterity", "skill": "defense"},
    "parry": {"stat": "dexterity", "skill": "swordsmanship"},
    "persuade": {"stat": "charisma", "skill": "persuasion"},
    "intimidate": {"stat": "charisma", "skill": "intimidation"},
    "deceive": {"stat": "charisma", "skill": "persuasion"},
    "sneak": {"stat": "dexterity", "skill": "stealth"},
    "investigate": {"stat": "intelligence", "skill": "investigation"},
    "hack": {"stat": "intelligence", "skill": "hacking"},
    "cast_spell": {"stat": "intelligence", "skill": "magic"},
    "use_item": {"stat": "intelligence", "skill": "investigation"},
    "pickup_item": {"stat": "dexterity", "skill": "investigation"},
    "equip_item": {"stat": "dexterity", "skill": "defense"},
    "unequip_item": {"stat": "dexterity", "skill": "defense"},
    "social_activity": {"stat": "charisma", "skill": "persuasion"},
    "social_competition": {"stat": "charisma", "skill": "persuasion"},
    "social_affection": {"stat": "charisma", "skill": "persuasion"},
    "social_performance": {"stat": "charisma", "skill": "persuasion"},
    "trade": {"stat": "charisma", "skill": "persuasion"},
    "ritual": {"stat": "intelligence", "skill": "magic"},
    "exploration": {"stat": "intelligence", "skill": "investigation"},
    "threat": {"stat": "charisma", "skill": "intimidation"},
    # Legacy aliases
    "attack": {"stat": "strength", "skill": "swordsmanship"},
    "steal": {"stat": "dexterity", "skill": "stealth"},
}

_DIFFICULTY_DC: Dict[str, int] = {
    "trivial": 3,
    "easy": 6,
    "normal": 10,
    "hard": 14,
    "very_hard": 18,
    "legendary": 22,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _get_stat(actor: Dict[str, Any], stat_name: str) -> int:
    stats = _safe_dict(actor.get("stats"))
    return _safe_int(stats.get(stat_name), 5)


def _get_skill_level(actor: Dict[str, Any], skill_id: str) -> int:
    skills = _safe_dict(actor.get("skills"))
    skill = skills.get(skill_id, {})
    if isinstance(skill, dict):
        return _safe_int(skill.get("level"), 0)
    return _safe_int(skill, 0)


def _make_rng(seed: int | random.Random) -> random.Random:
    if isinstance(seed, random.Random):
        return seed
    if seed is None:
        raise ValueError("an explicit RPG action RNG seed is required")
    return random.Random(seed)


# ---------------------------------------------------------------------------
# Damage Helpers
# ---------------------------------------------------------------------------

def apply_damage(target: Any, amount: int) -> int:
    """Apply *amount* damage to *target* (PlayerState or dict). Clamps hp at 0."""
    current_hp = getattr(target, "hp", None)
    if current_hp is not None:
        actual = min(amount, current_hp)
        target.hp = max(0, current_hp - amount)
        return actual
    if isinstance(target, dict) and "hp" in target:
        actual = min(amount, target["hp"])
        target["hp"] = max(0, target["hp"] - amount)
        return actual
    return 0


# ---------------------------------------------------------------------------
# Weapon / Defense helpers
# ---------------------------------------------------------------------------

def select_equipped_weapon(player_state: Dict[str, Any]) -> Dict[str, Any]:
    """Return the currently equipped weapon, or a bare-hands fallback."""
    ps = _safe_dict(player_state)
    inv = _safe_dict(ps.get("inventory_state"))
    equipment = _safe_dict(inv.get("equipment"))
    main_hand = _safe_dict(equipment.get("main_hand"))
    if main_hand.get("item_id"):
        return main_hand
    return {
        "item_id": "unarmed",
        "name": "Unarmed",
        "combat_stats": {
            "weapon_type": "unarmed",
            "attack_stat": "strength",
            "skill_id": "",
            "damage": 3,
            "accuracy": 1,
            "crit_chance": 3,
            "crit_bonus": 0,
            "range": 1,
            "armor_penetration": 0,
        },
        "quality": {"tier": 0, "rarity": "common"},
    }


def compute_defense_rating(actor_state: Dict[str, Any]) -> int:
    """Compute total defense rating from armor + stats."""
    actor = _safe_dict(actor_state)
    base_def = _safe_int(actor.get("defense", 0))
    con_mod = max(0, (_get_stat(actor, "constitution") - 10) // 2)
    inv = _safe_dict(actor.get("inventory_state"))
    equipment = _safe_dict(inv.get("equipment"))
    armor_def = 0
    for slot_name, slot_item in equipment.items():
        if isinstance(slot_item, dict) and slot_name != "main_hand":
            cs = _safe_dict(slot_item.get("combat_stats"))
            armor_def += _safe_int(cs.get("defense_bonus"))
    return base_def + con_mod + armor_def


def compute_weapon_damage(weapon_state: Dict[str, Any], attacker_state: Dict[str, Any], outcome: Dict[str, Any]) -> int:
    """Compute final weapon damage given outcome (hit/crit info)."""
    weapon = _safe_dict(weapon_state)
    attacker = _safe_dict(attacker_state)
    cs = _safe_dict(weapon.get("combat_stats"))
    quality = _safe_dict(weapon.get("quality"))

    base_damage = _safe_int(cs.get("damage"), 3)
    attack_stat = str(cs.get("attack_stat", "strength"))
    stat_bonus = max(0, (_get_stat(attacker, attack_stat) - 10) // 2)
    skill_id = str(cs.get("skill_id", "swordsmanship"))
    skill_bonus = _get_skill_level(attacker, skill_id) // 2
    quality_bonus = _safe_int(quality.get("tier"))
    is_crit = bool(outcome.get("is_crit"))
    crit_bonus = _safe_int(cs.get("crit_bonus")) if is_crit else 0
    target_armor = _safe_int(outcome.get("target_armor", 0))
    pen = _safe_int(cs.get("armor_penetration"))
    effective_armor = max(0, target_armor - pen)

    return max(0, base_damage + stat_bonus + skill_bonus + quality_bonus + crit_bonus - effective_armor)


# ---------------------------------------------------------------------------
# Core Resolution
# ---------------------------------------------------------------------------

def resolve_attack_roll(
    attacker: Dict[str, Any],
    defender: Dict[str, Any],
    weapon: Dict[str, Any],
    seed: int | random.Random,
) -> Dict[str, Any]:
    """Deterministic attack resolution. Returns hit/miss/crit/graze + damage."""
    rng = _make_rng(seed)
    attacker = _safe_dict(attacker)
    defender = _safe_dict(defender)
    weapon = _safe_dict(weapon)
    cs = _safe_dict(weapon.get("combat_stats"))

    attack_stat = str(cs.get("attack_stat", "strength"))
    skill_id = str(cs.get("skill_id", "swordsmanship"))

    stat_mod = (_get_stat(attacker, attack_stat) - 10) // 2
    skill_mod = _get_skill_level(attacker, skill_id) // 2
    accuracy = _safe_int(cs.get("accuracy"))
    attack_roll = rng.randint(1, 20) + stat_mod + skill_mod + accuracy

    def_dex_mod = (_get_stat(defender, "dexterity") - 10) // 2
    def_rating = compute_defense_rating(defender)
    defense_total = 10 + def_dex_mod + def_rating

    crit_threshold = max(1, 20 - _safe_int(cs.get("crit_chance", 5)) // 5)
    raw_roll = attack_roll - stat_mod - skill_mod - accuracy
    is_crit = raw_roll >= crit_threshold

    if attack_roll >= defense_total + 5 or is_crit:
        outcome_type = "crit" if is_crit else "hit"
    elif attack_roll >= defense_total:
        outcome_type = "hit"
    elif attack_roll >= defense_total - 3:
        outcome_type = "graze"
    else:
        outcome_type = "miss"

    target_armor = def_rating
    damage = 0
    if outcome_type in ("hit", "crit"):
        damage = compute_weapon_damage(weapon, attacker, {"is_crit": is_crit, "target_armor": target_armor})
    elif outcome_type == "graze":
        damage = max(1, compute_weapon_damage(weapon, attacker, {"is_crit": False, "target_armor": target_armor}) // 2)

    return {
        "outcome": outcome_type,
        "attack_roll": attack_roll,
        "defense_total": defense_total,
        "is_crit": is_crit,
        "damage": damage,
        "weapon_id": str(weapon.get("item_id", "unarmed")),
        "skill_id": skill_id,
        "stat_used": attack_stat,
        "stat_modifier": stat_mod,
        "skill_modifier": skill_mod,
    }


def resolve_noncombat_check(
    player_state: Dict[str, Any],
    action_type: str,
    difficulty: str = "normal",
    *,
    seed: int | random.Random,
) -> Dict[str, Any]:
    """Resolve a non-combat skill check (persuade, sneak, hack, etc.)."""
    rng = _make_rng(seed)
    player = _safe_dict(player_state)
    profile = _ACTION_PROFILES.get(str(action_type), {"stat": "intelligence", "skill": "investigation"})
    stat_name = profile["stat"]
    skill_name = profile["skill"]

    stat_mod = (_get_stat(player, stat_name) - 10) // 2
    skill_mod = _get_skill_level(player, skill_name) // 2
    dc = _DIFFICULTY_DC.get(str(difficulty), 10)

    roll = rng.randint(1, 20) + stat_mod + skill_mod
    margin = roll - dc

    if margin >= 10:
        outcome = "critical_success"
    elif margin >= 0:
        outcome = "success"
    elif margin >= -3:
        outcome = "partial"
    else:
        outcome = "failure"

    return {
        "outcome": outcome,
        "roll": roll,
        "dc": dc,
        "margin": margin,
        "action_type": str(action_type),
        "stat_used": stat_name,
        "skill_id": skill_name,
        "stat_modifier": stat_mod,
        "skill_modifier": skill_mod,
        "difficulty": str(difficulty),
    }


def resolve_player_action(
    simulation_state: Dict[str, Any],
    action: Dict[str, Any],
    seed: int | random.Random,
) -> Dict[str, Any]:
    """Main entry point: resolve any player action type."""
    sim = _safe_dict(simulation_state)
    action = _safe_dict(action)
    action_type = str(action.get("action_type", action.get("type", "investigate")))
    player_state = _safe_dict(sim.get("player_state"))

    def _target_name(target: Dict[str, Any]) -> str:
        target = _safe_dict(target)
        return str(
            target.get("name")
            or target.get("npc_name")
            or target.get("display_name")
            or target.get("npc_id")
            or target.get("id")
            or ""
        )

    def _success_like(outcome: str) -> bool:
        return str(outcome or "") in {"success", "critical_success", "hit", "crit"}

    combat_actions = {"attack_melee", "attack_ranged", "attack", "attack_unarmed", "block", "parry"}
    if action_type in combat_actions:
        weapon = select_equipped_weapon(player_state)
        defender = _safe_dict(action.get("target") or action.get("defender") or {})
        if not defender.get("stats"):
            defender.setdefault("stats", {"dexterity": 10, "constitution": 10})
        if "hp" not in defender:
            defender["hp"] = 20
        result = resolve_attack_roll(player_state, defender, weapon, seed)
        if result["damage"] > 0 and "hp" in defender:
            apply_damage(defender, result["damage"])
            result["defender_hp_after"] = _safe_int(defender.get("hp"))
        result["target_name"] = _target_name(defender)
        result["enemy_state"] = {
            "difficulty_tier": _safe_int(defender.get("difficulty_tier"), 1),
            "name": _target_name(defender),
        }
        result["enemy_difficulty_tier"] = _safe_int(defender.get("difficulty_tier"), 1)
        result["enemy_defeated"] = bool(
            result.get("damage", 0) > 0
            and "hp" in defender
            and _safe_int(defender.get("hp"), 0) <= 0
        )
        result["action_type"] = action_type
        # Special handling for unarmed attacks
        if action_type == "attack_unarmed":
            result["skill_id"] = ""
        return {"result": result, "simulation_state": sim}

    item_actions = {"pickup_item", "equip_item", "unequip_item", "use_item"}
    if action_type in item_actions:
        return {
            "result": {
                "outcome": "success",
                "action_type": action_type,
                "item_id": str(action.get("item_id", "")),
                "skill_id": "investigation",
                "stat_used": "dexterity",
            },
            "simulation_state": sim,
        }

    difficulty = str(action.get("difficulty", "normal"))
    result = resolve_noncombat_check(player_state, action_type, difficulty, seed=seed)
    result["target_name"] = _target_name(_safe_dict(action.get("target")))
    if not result["target_name"]:
        result["target_name"] = str(
            action.get("target_name")
            or action.get("npc_name")
            or action.get("target_id")
            or ""
        )
    result["target_id"] = str(
        action.get("target_id")
        or _safe_dict(action.get("target")).get("id")
        or _safe_dict(action.get("target")).get("npc_id")
        or ""
    )
    result["interaction"] = str(action.get("interaction", ""))
    result["semantic_action"] = _safe_dict(action.get("semantic_action"))

    # Special handling for observation
    if action_type == "observe":
        result["skill_id"] = ""
        result["discovery"] = False
        result["important_find"] = False
    
    if action_type == "steal" and _success_like(cast(Any, result.get("outcome"))):
        gold_stolen = _safe_int(action.get("gold_stolen") or action.get("gold_amount"), 0)
        loot_value = _safe_int(action.get("loot_value"), 0)
        result["gold_stolen"] = gold_stolen
        result["loot_value"] = loot_value

    if action_type == "investigate" and _success_like(cast(Any, result.get("outcome"))):
        result["found_clue"] = bool(action.get("found_clue"))
        result["discovery"] = str(action.get("discovery") or "")
        result["revealed"] = bool(action.get("revealed"))
        result["important_find"] = bool(
            action.get("important_find")
            or action.get("found_clue")
            or action.get("revealed")
            or action.get("discovery")
        )

    return {"result": result, "simulation_state": sim}


# ---------------------------------------------------------------------------
# Legacy compatibility
# ---------------------------------------------------------------------------

def resolve_action(
    player,
    action_type: str,
    difficulty: str = "normal",
    seed: int | random.Random = 0,
) -> Dict[str, Any]:
    """Legacy wrapper for old PlayerState-based calls."""
    if isinstance(player, dict):
        ps = player
    else:
        ps = {
            "stats": {
                "strength": getattr(getattr(player, 'stats', None), 'strength', 5),
                "dexterity": getattr(getattr(player, 'stats', None), 'dexterity', 5),
                "constitution": getattr(getattr(player, 'stats', None), 'constitution', 5),
                "intelligence": getattr(getattr(player, 'stats', None), 'intelligence', 5),
                "wisdom": getattr(getattr(player, 'stats', None), 'wisdom', 5),
                "charisma": getattr(getattr(player, 'stats', None), 'charisma', 5),
            },
            "skills": getattr(player, 'skills', {}),
        }
    result = resolve_noncombat_check(ps, action_type, difficulty, seed=seed)
    damage = 0
    if action_type in ("attack", "attack_melee") and result["outcome"] in ("success", "critical_success"):
        damage = 5 + _get_stat(ps, "strength")
    return {
        "type": action_type,
        "result": {"success": result["outcome"] in ("success", "critical_success"), **result},
        "damage": damage,
        "stat": result["stat_used"],
        "skill": result["skill_id"],
    }
