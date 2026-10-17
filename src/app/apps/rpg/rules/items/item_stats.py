"""Phase 18.3A — Item combat/equipment stat normalization."""
from __future__ import annotations

from typing import Any, Dict

_DEFAULT_COMBAT_STATS = {
    "weapon_type": "", "attack_stat": "strength", "skill_id": "swordsmanship",
    "damage": 0, "accuracy": 0, "crit_chance": 5, "crit_bonus": 0,
    "range": 1, "armor_penetration": 0, "defense_bonus": 0,
    "block_bonus": 0, "speed_bonus": 0,
}
_DEFAULT_EQUIPMENT = {"slot": "", "two_handed": False}
_DEFAULT_QUALITY = {"tier": 0, "rarity": "common"}

_WEAPON_SKILL_MAP = {
    "sword": "swordsmanship", "axe": "swordsmanship", "mace": "swordsmanship",
    "dagger": "swordsmanship", "spear": "swordsmanship",
    "bow": "archery", "crossbow": "archery",
    "pistol": "firearms", "rifle": "firearms", "shotgun": "firearms", "smartgun": "firearms",
    "staff": "magic", "wand": "magic",
}
_WEAPON_STAT_MAP = {
    "sword": "strength", "axe": "strength", "mace": "strength",
    "dagger": "dexterity", "spear": "strength",
    "bow": "dexterity", "crossbow": "dexterity",
    "pistol": "dexterity", "rifle": "dexterity", "shotgun": "strength", "smartgun": "intelligence",
    "staff": "intelligence", "wand": "intelligence",
}

def normalize_item_stats(item_def: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure item_def has normalized combat_stats, equipment, quality fields."""
    item_def = dict(item_def or {})
    combat = dict(item_def.get("combat_stats") or {})
    for k, v in _DEFAULT_COMBAT_STATS.items():
        combat.setdefault(k, v)
    # Clamp numeric fields
    for k in ["damage", "accuracy", "crit_chance", "crit_bonus", "range", "armor_penetration", "defense_bonus", "block_bonus", "speed_bonus"]:
        combat[k] = max(0, int(combat.get(k, 0)))
    item_def["combat_stats"] = combat
    equip = dict(item_def.get("equipment") or {})
    for k, v in _DEFAULT_EQUIPMENT.items():
        equip.setdefault(k, v)
    item_def["equipment"] = equip
    quality = dict(item_def.get("quality") or {})
    for k, v in _DEFAULT_QUALITY.items():
        quality.setdefault(k, v)
    quality["tier"] = max(0, min(10, int(quality.get("tier", 0))))
    item_def["quality"] = quality
    return item_def

def is_weapon(item_def: Dict[str, Any]) -> bool:
    cs = (item_def or {}).get("combat_stats", {})
    return isinstance(cs, dict) and int(cs.get("damage", 0)) > 0

def is_armor(item_def: Dict[str, Any]) -> bool:
    cs = (item_def or {}).get("combat_stats", {})
    return isinstance(cs, dict) and int(cs.get("defense_bonus", 0)) > 0

def is_shield(item_def: Dict[str, Any]) -> bool:
    cs = (item_def or {}).get("combat_stats", {})
    return isinstance(cs, dict) and int(cs.get("block_bonus", 0)) > 0

def get_weapon_skill(item_def: Dict[str, Any]) -> str:
    cs = (item_def or {}).get("combat_stats", {})
    if isinstance(cs, dict) and cs.get("skill_id"):
        return str(cs["skill_id"])
    wt = str(cs.get("weapon_type", "")) if isinstance(cs, dict) else ""
    return _WEAPON_SKILL_MAP.get(wt, "swordsmanship")

def get_weapon_attack_stat(item_def: Dict[str, Any]) -> str:
    cs = (item_def or {}).get("combat_stats", {})
    if isinstance(cs, dict) and cs.get("attack_stat"):
        return str(cs["attack_stat"])
    wt = str(cs.get("weapon_type", "")) if isinstance(cs, dict) else ""
    return _WEAPON_STAT_MAP.get(wt, "strength")
