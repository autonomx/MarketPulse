"""Deterministic RPG equipment slots and derived stats."""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

DERIVED_STAT_KEYS = {
    "initiative_modifier",
    "stealth_modifier",
    "social_modifier",
}
SKILL_MODIFIER_KEYS = {"archery", "melee", "defense", "alchemy", "crafting", "survival", "lockpicking", "persuasion", "stealth"}
MECHANICAL_EQUIPMENT_FIELDS = {"damage", "defense", "resistances", "stats", "modifiers", "capabilities", "rarity", "quality"}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def display_equipment_name(item: dict[str, Any]) -> str:
    display = _safe_dict(item.get("display"))
    return _text(item.get("name") or item.get("label") or item.get("display_name") or display.get("name") or item.get("item_id") or item.get("id"), "Item")


def resolve_equipment_slot(item: dict[str, Any]) -> str:
    requested = _text(item.get("slot") or item.get("equip_slot"))
    if requested:
        return requested
    raw = _norm(item.get("item_type") or item.get("type") or item.get("category") or display_equipment_name(item))
    name = _norm(display_equipment_name(item))
    if any(token in raw or token in name for token in ("bow", "dagger", "sword", "axe", "weapon", "staff", "wand", "mace", "spear")):
        return "Weapon"
    if any(token in raw or token in name for token in ("armor", "mail", "leather", "plate", "robe")):
        return "Armor"
    if "shield" in raw or "shield" in name:
        return "Shield"
    if "cloak" in raw or "cloak" in name:
        return "Cloak"
    if "ring" in raw or "band" in name:
        return "Ring"
    if "tool" in raw:
        return "Tool"
    return "Utility"


def _equipment_entry(item: dict[str, Any], slot: str) -> dict[str, Any]:
    entry = {"slot": slot, "name": display_equipment_name(item)}
    for key in (
        "item_id",
        "id",
        "item_type",
        "type",
        "weapon_type",
        "armor_type",
        "rarity",
        "quality",
        "damage",
        "defense",
        "resistances",
        "stats",
        "modifiers",
        "capabilities",
    ):
        if key in item:
            entry[key] = deepcopy(item[key])
    if not any(field in entry for field in MECHANICAL_EQUIPMENT_FIELDS):
        return {"slot": slot, "name": entry["name"]}
    return entry


def normalize_equipment(items: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in _safe_list(items):
        if isinstance(raw, dict):
            item = deepcopy(raw)
            item.setdefault("slot", resolve_equipment_slot(item))
            item.setdefault("name", display_equipment_name(item))
            normalized.append(item)
        else:
            normalized.append({"slot": "Utility", "name": str(raw)})
    return normalized


def _add_profile(target: dict[str, int], raw: Any) -> None:
    if isinstance(raw, dict):
        if "type" in raw and "amount" in raw:
            key = _text(raw.get("type"))
            if key:
                target[key] = target.get(key, 0) + int(raw.get("amount") or 0)
            return
        for key, value in raw.items():
            if isinstance(value, (int, float)):
                target[str(key)] = target.get(str(key), 0) + int(value)
    elif isinstance(raw, list):
        for entry in raw:
            _add_profile(target, entry)


def _add_modifiers(summary: dict[str, Any], raw: Any) -> None:
    modifiers = _safe_dict(raw)
    for key, value in modifiers.items():
        if not isinstance(value, (int, float)):
            continue
        if key in DERIVED_STAT_KEYS:
            summary[key] = int(summary.get(key) or 0) + int(value)
        elif key in SKILL_MODIFIER_KEYS:
            skills = _safe_dict(summary.get("skill_modifiers"))
            skills[key] = int(skills.get(key) or 0) + int(value)
            summary["skill_modifiers"] = skills


def build_equipment_derived_stats(equipment: list[Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "damage_profile": {},
        "defense_profile": {},
        "resistances": {},
        "initiative_modifier": 0,
        "stealth_modifier": 0,
        "social_modifier": 0,
        "skill_modifiers": {},
        "sources": [],
        "mechanics_source": "engine_equipment_derived_stats_v1",
    }
    for raw in normalize_equipment(equipment):
        item = _safe_dict(raw)
        _add_profile(summary["damage_profile"], item.get("damage"))
        _add_profile(summary["defense_profile"], item.get("defense"))
        _add_profile(summary["resistances"], item.get("resistances"))
        _add_modifiers(summary, item.get("stats"))
        _add_modifiers(summary, item.get("modifiers"))
        if item.get("damage") or item.get("defense") or item.get("stats") or item.get("modifiers") or item.get("resistances"):
            summary["sources"].append({"slot": item.get("slot"), "name": item.get("name"), "item_id": item.get("item_id") or item.get("id")})
    return summary


def equip_item_for_player(player: dict[str, Any], item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    equipment = normalize_equipment(_safe_list(player.get("equipment")))
    slot = resolve_equipment_slot(item)
    equipped = _equipment_entry(item, slot)
    for index, existing in enumerate(equipment):
        if _norm(existing.get("slot")) == _norm(slot):
            equipment[index] = equipped
            break
    else:
        equipment.append(equipped)
    player["equipment"] = equipment
    derived = build_equipment_derived_stats(equipment)
    player["derived_stats"] = derived
    return slot, derived
