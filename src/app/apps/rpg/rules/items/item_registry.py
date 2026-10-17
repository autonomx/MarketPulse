"""Phase 9.0 — Deterministic static item registry.

All item definitions are serialisable, no randomness or generated fields.
"""
from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict


# Deterministic static item registry.
# No randomness, no generated fields, all serialisable.
_ITEM_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "gold_coin": {
        "item_id": "gold_coin",
        "name": "Gold Coin",
        "category": "currency",
        "stackable": True,
        "max_stack": 9999,
        "tags": ["currency"],
    },
    "healing_potion": {
        "item_id": "healing_potion",
        "name": "Healing Potion",
        "category": "consumable",
        "stackable": True,
        "max_stack": 10,
        "tags": ["consumable", "healing"],
        "effect": {
            "type": "restore_resource",
            "resource": "health",
            "amount": 10,
        },
    },
    "ration": {
        "item_id": "ration",
        "name": "Trail Ration",
        "category": "consumable",
        "stackable": True,
        "max_stack": 20,
        "tags": ["consumable", "food", "survival"],
        "description": "A compact meal for road travel.",
        "effect": {
            "type": "reduce_survival_pressure",
            "resource": "hunger",
            "amount": 35,
        },
        "value": 2,
    },
    "water_skin": {
        "item_id": "water_skin",
        "name": "Water Skin",
        "category": "consumable",
        "stackable": True,
        "max_stack": 10,
        "tags": ["consumable", "water", "survival"],
        "description": "Clean drinking water sealed in a travel skin.",
        "effect": {
            "type": "reduce_survival_pressure",
            "resource": "thirst",
            "amount": 40,
        },
        "value": 1,
    },
    "bandit_token": {
        "item_id": "bandit_token",
        "name": "Bandit Token",
        "category": "quest",
        "stackable": True,
        "max_stack": 20,
        "tags": ["quest", "proof"],
    },
    "rusty_sword": {
        "item_id": "rusty_sword", "name": "Rusty Sword", "category": "weapon",
        "stackable": False, "max_stack": 1, "tags": ["weapon", "melee", "sword"],
        "combat_stats": {"weapon_type": "sword", "attack_stat": "strength", "skill_id": "swordsmanship", "damage": 12, "accuracy": 2, "crit_chance": 5, "crit_bonus": 4, "range": 1, "armor_penetration": 0, "defense_bonus": 0, "block_bonus": 0, "speed_bonus": 0},
        "equipment": {"slot": "main_hand", "two_handed": False},
        "quality": {"tier": 0, "rarity": "common"},
        "value": 5,
    },
    "iron_sword": {
        "item_id": "iron_sword", "name": "Iron Sword", "category": "weapon",
        "stackable": False, "max_stack": 1, "tags": ["weapon", "melee", "sword"],
        "combat_stats": {"weapon_type": "sword", "attack_stat": "strength", "skill_id": "swordsmanship", "damage": 28, "accuracy": 5, "crit_chance": 8, "crit_bonus": 8, "range": 1, "armor_penetration": 2, "defense_bonus": 0, "block_bonus": 0, "speed_bonus": 0},
        "equipment": {"slot": "main_hand", "two_handed": False},
        "quality": {"tier": 1, "rarity": "common"},
        "value": 25,
    },
    "wooden_shield": {
        "item_id": "wooden_shield", "name": "Wooden Shield", "category": "armor",
        "stackable": False, "max_stack": 1, "tags": ["armor", "shield"],
        "combat_stats": {"weapon_type": "", "attack_stat": "", "skill_id": "defense", "damage": 0, "accuracy": 0, "crit_chance": 0, "crit_bonus": 0, "range": 0, "armor_penetration": 0, "defense_bonus": 3, "block_bonus": 8, "speed_bonus": -1},
        "equipment": {"slot": "off_hand", "two_handed": False},
        "quality": {"tier": 0, "rarity": "common"},
        "value": 8,
    },
    "iron_shield": {
        "item_id": "iron_shield", "name": "Iron Shield", "category": "armor",
        "stackable": False, "max_stack": 1, "tags": ["armor", "shield"],
        "combat_stats": {"weapon_type": "", "attack_stat": "", "skill_id": "defense", "damage": 0, "accuracy": 0, "crit_chance": 0, "crit_bonus": 0, "range": 0, "armor_penetration": 0, "defense_bonus": 6, "block_bonus": 14, "speed_bonus": -2},
        "equipment": {"slot": "off_hand", "two_handed": False},
        "quality": {"tier": 1, "rarity": "common"},
        "value": 30,
    },
    "short_bow": {
        "item_id": "short_bow", "name": "Short Bow", "category": "weapon",
        "stackable": False, "max_stack": 1, "tags": ["weapon", "ranged", "bow"],
        "combat_stats": {"weapon_type": "bow", "attack_stat": "dexterity", "skill_id": "archery", "damage": 15, "accuracy": 6, "crit_chance": 10, "crit_bonus": 5, "range": 3, "armor_penetration": 1, "defense_bonus": 0, "block_bonus": 0, "speed_bonus": 1},
        "equipment": {"slot": "main_hand", "two_handed": True},
        "quality": {"tier": 0, "rarity": "common"},
        "value": 15,
    },
    "pistol_9mm": {
        "item_id": "pistol_9mm", "name": "9mm Pistol", "category": "weapon",
        "stackable": False, "max_stack": 1, "tags": ["weapon", "ranged", "firearm"],
        "combat_stats": {"weapon_type": "pistol", "attack_stat": "dexterity", "skill_id": "firearms", "damage": 20, "accuracy": 7, "crit_chance": 8, "crit_bonus": 10, "range": 4, "armor_penetration": 3, "defense_bonus": 0, "block_bonus": 0, "speed_bonus": 2},
        "equipment": {"slot": "main_hand", "two_handed": False},
        "quality": {"tier": 1, "rarity": "uncommon"},
        "value": 50,
    },
    "combat_knife": {
        "item_id": "combat_knife", "name": "Combat Knife", "category": "weapon",
        "stackable": False, "max_stack": 1, "tags": ["weapon", "melee", "dagger"],
        "combat_stats": {"weapon_type": "dagger", "attack_stat": "dexterity", "skill_id": "swordsmanship", "damage": 8, "accuracy": 8, "crit_chance": 15, "crit_bonus": 10, "range": 1, "armor_penetration": 1, "defense_bonus": 0, "block_bonus": 0, "speed_bonus": 3},
        "equipment": {"slot": "main_hand", "two_handed": False},
        "quality": {"tier": 0, "rarity": "common"},
        "value": 10,
    },
}


def get_item_definition(item_id: str) -> Dict[str, Any]:
    """Return a copy of the item definition for *item_id*, or an empty dict."""
    item_id = str(item_id or "")
    return _safe_dict(_ITEM_DEFINITIONS.get(item_id))


def list_item_definitions() -> Dict[str, Dict[str, Any]]:
    """Return a sorted copy of every item definition, keyed by item_id."""
    return {
        str(item_id): _safe_dict(item_def)
        for item_id, item_def in sorted(_ITEM_DEFINITIONS.items(), key=lambda kv: str(kv[0]))
    }


def normalize_item_definition(item_def: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize any item definition with full combat stats schema."""
    from .item_stats import normalize_item_stats
    item_def = dict(item_def or {})
    item_def.setdefault("item_id", "")
    item_def.setdefault("name", "")
    item_def.setdefault("category", "misc")
    item_def.setdefault("stackable", False)
    item_def.setdefault("max_stack", 1)
    item_def.setdefault("tags", [])
    item_def.setdefault("value", 0)
    return normalize_item_stats(item_def)
