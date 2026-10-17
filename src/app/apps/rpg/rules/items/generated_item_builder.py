"""Phase 18.3A — LLM-generated item normalization and clamping."""
from __future__ import annotations

import hashlib
from typing import Any, Dict

from .item_stats import normalize_item_stats

_MAX_DAMAGE = {0: 20, 1: 35, 2: 50, 3: 70, 4: 100}
_MAX_DEFENSE = {0: 10, 1: 20, 2: 30, 3: 45, 4: 60}
_RARITY_TIERS = {"common": 0, "uncommon": 1, "rare": 2, "epic": 3, "legendary": 4}

def derive_item_power_band(world_tier: int = 0, rarity: str = "common") -> Dict[str, int]:
    """Return max damage/defense bounds for a given world tier + rarity."""
    world_tier = max(0, min(4, int(world_tier)))
    rarity = str(rarity).lower()
    rarity_bonus = _RARITY_TIERS.get(rarity, 0)
    effective = min(4, world_tier + rarity_bonus)
    return {"max_damage": _MAX_DAMAGE.get(effective, 20), "max_defense": _MAX_DEFENSE.get(effective, 10)}

def clamp_generated_item_stats(item_def: Dict[str, Any], world_tier: int = 0) -> Dict[str, Any]:
    """Clamp generated item stats to power band."""
    item_def = normalize_item_stats(dict(item_def or {}))
    rarity = str((item_def.get("quality") or {}).get("rarity", "common"))
    band = derive_item_power_band(world_tier, rarity)
    cs = dict(item_def.get("combat_stats") or {})
    cs["damage"] = min(cs.get("damage", 0), band["max_damage"])
    cs["defense_bonus"] = min(cs.get("defense_bonus", 0), band["max_defense"])
    cs["block_bonus"] = min(cs.get("block_bonus", 0), band["max_defense"])
    cs["accuracy"] = min(max(0, cs.get("accuracy", 0)), 20)
    cs["crit_chance"] = min(max(0, cs.get("crit_chance", 0)), 30)
    cs["crit_bonus"] = min(max(0, cs.get("crit_bonus", 0)), 50)
    item_def["combat_stats"] = cs
    return item_def

def build_item_definition_from_llm(payload: Dict[str, Any], world_tier: int = 0) -> Dict[str, Any]:
    """Build a normalized item definition from LLM payload, clamped to power band."""
    payload = dict(payload or {})
    # Deterministic ID from content
    content_str = f"{payload.get('name', '')}-{payload.get('category', '')}-{payload.get('combat_stats', {}).get('weapon_type', '')}"
    item_id = payload.get("item_id") or ("gen_" + hashlib.sha256(content_str.encode()).hexdigest()[:12])
    item_def = {
        "item_id": str(item_id),
        "name": str(payload.get("name", "Unknown Item")),
        "category": str(payload.get("category", "misc")),
        "stackable": bool(payload.get("stackable", False)),
        "max_stack": max(1, int(payload.get("max_stack", 1) or 1)),
        "tags": [str(t) for t in (payload.get("tags") or [])[:8]],
        "description": str(payload.get("description", "")),
        "value": max(0, int(payload.get("value", 0) or 0)),
        "durability": max(0, int(payload.get("durability", 100) or 100)),
        "generated_by": "llm",
        "stat_origin": "generated",
    }
    if payload.get("combat_stats"):
        item_def["combat_stats"] = payload["combat_stats"]
    if payload.get("equipment"):
        item_def["equipment"] = payload["equipment"]
    if payload.get("quality"):
        item_def["quality"] = payload["quality"]
    return clamp_generated_item_stats(item_def, world_tier)
