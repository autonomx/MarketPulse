from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


ENEMY_ARCHETYPES: Dict[str, Dict[str, Any]] = {
    "enemy:bandit_grunt": {
        "archetype_id": "enemy:bandit_grunt",
        "name": "Bandit Grunt",
        "level": 1,
        "side": "enemy",
        "hp": 10,
        "max_hp": 10,
        "defense": 10,
        "armor": 0,
        "damage_min": 1,
        "damage_max": 4,
        "accuracy_bonus": 1,
        "initiative_bonus": 0,
        "morale_threshold": 35,
        "tags": ["humanoid", "bandit"],
        "loot_table_id": "loot:bandit_common",
        "xp_value": 25,
        "budget_cost": 25,
        "condition_immunities": [],
    },
    "enemy:bandit_archer": {
        "archetype_id": "enemy:bandit_archer",
        "name": "Bandit Archer",
        "level": 1,
        "side": "enemy",
        "hp": 8,
        "max_hp": 8,
        "defense": 9,
        "armor": 0,
        "damage_min": 1,
        "damage_max": 4,
        "accuracy_bonus": 2,
        "initiative_bonus": 1,
        "morale_threshold": 40,
        "tags": ["humanoid", "bandit", "ranged"],
        "loot_table_id": "loot:bandit_common",
        "xp_value": 25,
        "budget_cost": 25,
        "condition_immunities": [],
    },
    "enemy:bandit_brute": {
        "archetype_id": "enemy:bandit_brute",
        "name": "Bandit Brute",
        "level": 2,
        "side": "enemy",
        "hp": 18,
        "max_hp": 18,
        "defense": 9,
        "armor": 1,
        "damage_min": 2,
        "damage_max": 6,
        "accuracy_bonus": 0,
        "initiative_bonus": -1,
        "morale_threshold": 30,
        "tags": ["humanoid", "bandit", "brute"],
        "loot_table_id": "loot:bandit_common",
        "xp_value": 50,
        "budget_cost": 50,
        "condition_immunities": [],
    },
    "enemy:wolf": {
        "archetype_id": "enemy:wolf",
        "name": "Wolf",
        "level": 1,
        "side": "enemy",
        "hp": 9,
        "max_hp": 9,
        "defense": 11,
        "armor": 0,
        "damage_min": 1,
        "damage_max": 4,
        "accuracy_bonus": 2,
        "initiative_bonus": 2,
        "morale_threshold": 45,
        "tags": ["beast", "wolf"],
        "loot_table_id": "loot:wolf_common",
        "xp_value": 25,
        "budget_cost": 25,
        "condition_immunities": [],
    },
    "enemy:giant_rat": {
        "archetype_id": "enemy:giant_rat",
        "name": "Giant Rat",
        "level": 1,
        "side": "enemy",
        "hp": 5,
        "max_hp": 5,
        "defense": 10,
        "armor": 0,
        "damage_min": 1,
        "damage_max": 3,
        "accuracy_bonus": 1,
        "initiative_bonus": 1,
        "morale_threshold": 50,
        "tags": ["beast", "vermin"],
        "loot_table_id": "loot:vermin_common",
        "xp_value": 10,
        "budget_cost": 10,
        "condition_immunities": [],
    },
    "enemy:skeleton": {
        "archetype_id": "enemy:skeleton",
        "name": "Skeleton",
        "level": 1,
        "side": "enemy",
        "hp": 12,
        "max_hp": 12,
        "defense": 10,
        "armor": 1,
        "damage_min": 1,
        "damage_max": 5,
        "accuracy_bonus": 1,
        "initiative_bonus": 0,
        "morale_threshold": 0,
        "tags": ["undead"],
        "loot_table_id": "loot:undead_common",
        "xp_value": 30,
        "budget_cost": 30,
        "condition_immunities": ["bleeding", "poisoned"],
    },
    "enemy:cultist": {
        "archetype_id": "enemy:cultist",
        "name": "Cultist",
        "level": 1,
        "side": "enemy",
        "hp": 9,
        "max_hp": 9,
        "defense": 10,
        "armor": 0,
        "damage_min": 1,
        "damage_max": 4,
        "accuracy_bonus": 1,
        "initiative_bonus": 0,
        "morale_threshold": 25,
        "tags": ["humanoid", "cultist"],
        "loot_table_id": "loot:cultist_common",
        "xp_value": 25,
        "budget_cost": 25,
        "condition_immunities": [],
    },
    "enemy:guard": {
        "archetype_id": "enemy:guard",
        "name": "Guard",
        "level": 2,
        "side": "enemy",
        "hp": 16,
        "max_hp": 16,
        "defense": 12,
        "armor": 1,
        "damage_min": 2,
        "damage_max": 5,
        "accuracy_bonus": 2,
        "initiative_bonus": 0,
        "morale_threshold": 20,
        "tags": ["humanoid", "guard", "brave"],
        "loot_table_id": "loot:guard_common",
        "xp_value": 50,
        "budget_cost": 50,
        "condition_immunities": [],
    },
}


def get_enemy_archetype(archetype_id: str) -> Dict[str, Any]:
    archetype_id = _safe_str(archetype_id).strip()
    return deepcopy(_safe_dict(ENEMY_ARCHETYPES.get(archetype_id)))


def instantiate_enemy_from_archetype(
    archetype_id: str,
    *,
    instance_index: int = 1,
    actor_id: str = "",
) -> Dict[str, Any]:
    archetype = get_enemy_archetype(archetype_id)
    if not archetype:
        return {}

    base_id = archetype_id.replace("enemy:", "")
    actor_id = _safe_str(actor_id).strip() or f"{archetype_id}:{instance_index}"

    hp = _safe_int(archetype.get("hp"), _safe_int(archetype.get("max_hp"), 1))
    max_hp = _safe_int(archetype.get("max_hp"), hp)

    return {
        "actor_id": actor_id,
        "id": actor_id,
        "archetype_id": archetype_id,
        "name": _safe_str(archetype.get("name") or base_id).strip(),
        "side": _safe_str(archetype.get("side") or "enemy").strip(),
        "team": "enemy",
        "combat_team": "enemy",
        "level": _safe_int(archetype.get("level"), 1),
        "hp": hp,
        "max_hp": max_hp,
        "resources": {
            "hp": hp,
            "max_hp": max_hp,
        },
        "defense": _safe_int(archetype.get("defense"), 10),
        "armor": _safe_int(archetype.get("armor"), 0),
        "damage_min": _safe_int(archetype.get("damage_min"), 1),
        "damage_max": _safe_int(archetype.get("damage_max"), 4),
        "accuracy_bonus": _safe_int(archetype.get("accuracy_bonus"), 0),
        "initiative_bonus": _safe_int(archetype.get("initiative_bonus"), 0),
        "morale_threshold": _safe_int(archetype.get("morale_threshold"), 35),
        "tags": list(_safe_list(archetype.get("tags"))),
        "loot_table_id": _safe_str(archetype.get("loot_table_id")).strip(),
        "xp_value": _safe_int(archetype.get("xp_value"), 25),
        "budget_cost": _safe_int(archetype.get("budget_cost"), 25),
        "condition_immunities": list(_safe_list(archetype.get("condition_immunities"))),
        "status": "active",
        "status_effects": [],
    }