from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.interactions.equipment_runtime import project_equipment_stats
from app.apps.rpg.rules.interactions.item_model import (
    normalize_item_instance,
    recalculate_inventory_derived_fields,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _find_companion(simulation_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    party_state = _safe_dict(_safe_dict(simulation_state.get("player_state")).get("party_state"))
    for companion in _safe_list(party_state.get("companions")):
        companion = _safe_dict(companion)
        if _safe_str(companion.get("npc_id")) == npc_id:
            return companion
    return {}


def _ensure_inventory(companion: Dict[str, Any]) -> Dict[str, Any]:
    inventory = _safe_dict(companion.get("inventory"))
    if not isinstance(inventory.get("items"), list):
        inventory["items"] = []
    if not isinstance(inventory.get("equipment"), dict):
        inventory["equipment"] = {}
    inventory = recalculate_inventory_derived_fields(inventory)
    companion["inventory"] = inventory
    return inventory


def _find_item(items: List[Any], item_id: str) -> Dict[str, Any]:
    for item in _safe_list(items):
        item = normalize_item_instance(_safe_dict(item))
        if _safe_str(item.get("item_id")) == item_id:
            return item
    return {}


def _slot_for_item(item: Dict[str, Any]) -> str:
    item = normalize_item_instance(item)
    equipment = _safe_dict(item.get("equipment"))
    if _safe_str(equipment.get("slot")):
        return _safe_str(equipment.get("slot"))
    if _safe_str(item.get("slot")):
        return _safe_str(item.get("slot"))

    kind = _safe_str(item.get("kind")).lower()
    if kind == "weapon":
        return "main_hand"
    if kind == "armor":
        return "body"
    if kind == "shield":
        return "off_hand"
    return ""


def _score_item_for_slot(item: Dict[str, Any], slot: str) -> int:
    item = normalize_item_instance(item)
    equipment = _safe_dict(item.get("equipment"))
    stats = _safe_dict(equipment.get("stats"))

    if slot == "main_hand":
        return (
            _safe_int(stats.get("damage_max"), 0) * 10
            + _safe_int(stats.get("damage_min"), 0) * 5
            + _safe_int(stats.get("accuracy_bonus"), 0) * 3
            + _safe_int(stats.get("range"), 0)
        )

    if slot in {"body", "head", "cloak", "off_hand"}:
        return (
            _safe_int(stats.get("armor"), 0) * 10
            + _safe_int(stats.get("stealth_bonus"), 0) * 2
            - _safe_int(stats.get("stealth_penalty"), 0) * 2
        )

    if slot == "ammo":
        return _safe_int(item.get("quantity"), 1)

    return 0


def apply_companion_auto_equip(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    item_id: str,
    tick: int = 0,
) -> Dict[str, Any]:
    companion = _find_companion(simulation_state, npc_id)
    if not companion:
        return {
            "equipped": False,
            "changed_state": False,
            "reason": "companion_not_found",
            "npc_id": npc_id,
            "item_id": item_id,
            "source": "deterministic_companion_auto_equip",
        }

    inventory = _ensure_inventory(companion)
    items = [normalize_item_instance(_safe_dict(item)) for item in _safe_list(inventory.get("items"))]
    item = _find_item(items, item_id)
    if not item:
        return {
            "equipped": False,
            "changed_state": False,
            "reason": "item_not_in_companion_inventory",
            "npc_id": npc_id,
            "item_id": item_id,
            "source": "deterministic_companion_auto_equip",
        }

    slot = _slot_for_item(item)
    if not slot:
        return {
            "equipped": False,
            "changed_state": False,
            "reason": "item_not_equippable",
            "npc_id": npc_id,
            "item_id": item_id,
            "source": "deterministic_companion_auto_equip",
        }

    equipment = _safe_dict(inventory.get("equipment"))
    current_item_id = _safe_str(equipment.get(slot))

    incoming_score = _score_item_for_slot(item, slot)
    current_score = -1

    if current_item_id:
        current_item = _find_item(items, current_item_id)
        current_score = _score_item_for_slot(current_item, slot) if current_item else -1

    if current_item_id and incoming_score <= current_score:
        stats = project_equipment_stats(simulation_state, actor_id=npc_id)
        inventory["equipment_stats"] = stats
        companion["inventory"] = inventory
        return {
            "equipped": False,
            "changed_state": False,
            "reason": "not_better_than_current_equipment",
            "npc_id": npc_id,
            "item_id": item_id,
            "slot": slot,
            "incoming_score": incoming_score,
            "current_item_id": current_item_id,
            "current_score": current_score,
            "equipment_stats": deepcopy(stats),
            "source": "deterministic_companion_auto_equip",
        }

    equipment[slot] = item_id
    inventory["equipment"] = equipment
    inventory = recalculate_inventory_derived_fields(inventory)
    companion["inventory"] = inventory

    stats = project_equipment_stats(simulation_state, actor_id=npc_id)
    inventory["equipment_stats"] = stats
    companion["inventory"] = inventory

    return {
        "equipped": True,
        "changed_state": True,
        "reason": "better_equipment" if current_item_id else "empty_slot_equipped",
        "npc_id": npc_id,
        "item_id": item_id,
        "slot": slot,
        "incoming_score": incoming_score,
        "previous_item_id": current_item_id,
        "previous_score": current_score,
        "equipment_stats": deepcopy(stats),
        "tick": int(tick or 0),
        "source": "deterministic_companion_auto_equip",
    }
