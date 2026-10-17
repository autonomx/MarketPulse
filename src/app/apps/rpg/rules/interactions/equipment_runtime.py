from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.rules.interactions.item_model import (
    normalize_item_instance,
    recalculate_inventory_derived_fields,
    remove_quantity_from_items_list,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

DEFAULT_STATS = {
    "damage_min": 0,
    "damage_max": 1,
    "armor": 0,
    "accuracy_bonus": 0,
    "range": 1,
    "stealth_bonus": 0,
    "stealth_penalty": 0,
    "encumbrance_penalty": 0,
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _inventory_for_actor(simulation_state: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
    if actor_id in {"", "player"}:
        player_state = _safe_dict(simulation_state.get("player_state"))
        inv = _safe_dict(player_state.get("inventory"))
        if not isinstance(inv.get("items"), list):
            inv["items"] = []
        if not isinstance(inv.get("equipment"), dict):
            inv["equipment"] = {}
        player_state["inventory"] = inv
        simulation_state["player_state"] = player_state
        return inv

    party_state = _safe_dict(_safe_dict(simulation_state.get("player_state")).get("party_state"))
    for companion in _safe_list(party_state.get("companions")):
        companion = _safe_dict(companion)
        if _safe_str(companion.get("npc_id")) == actor_id:
            inv = _safe_dict(companion.get("inventory"))
            if not isinstance(inv.get("items"), list):
                inv["items"] = []
            if not isinstance(inv.get("equipment"), dict):
                inv["equipment"] = {}
            companion["inventory"] = inv
            return inv

    return {}


def _find_item(items, item_id: str) -> Dict[str, Any]:
    for item in _safe_list(items):
        item = normalize_item_instance(_safe_dict(item))
        if _safe_str(item.get("item_id")) == item_id:
            return item
    return {}


def _item_tags(item: Dict[str, Any]) -> set:
    return {_safe_str(tag) for tag in _safe_list(item.get("tags")) if _safe_str(tag)}


def project_equipment_stats(
    simulation_state: Dict[str, Any],
    *,
    actor_id: str = "player",
) -> Dict[str, Any]:
    inv = _inventory_for_actor(simulation_state, actor_id)
    items = [normalize_item_instance(_safe_dict(item)) for item in _safe_list(inv.get("items"))]
    equipment = _safe_dict(inv.get("equipment"))

    stats = deepcopy(DEFAULT_STATS)
    equipped_items = []

    for slot, item_id in equipment.items():
        item = _find_item(items, _safe_str(item_id))
        if not item:
            continue

        item_equipment = _safe_dict(item.get("equipment"))
        item_stats = _safe_dict(item_equipment.get("stats"))
        for key, value in item_stats.items():
            stats[key] = _safe_int(stats.get(key), 0) + _safe_int(value, 0)

        equipped_items.append({
            "slot": _safe_str(slot),
            "item_id": _safe_str(item_id),
            "definition_id": _safe_str(item.get("definition_id")),
            "name": _safe_str(item.get("name")),
            "stats": deepcopy(item_stats),
        })

    encumbrance = _safe_str(inv.get("encumbrance_state"))
    penalty = 0
    if encumbrance == "burdened":
        penalty = 1
    elif encumbrance == "overloaded":
        penalty = 3
    elif encumbrance == "immobile":
        penalty = 99

    stats["encumbrance_penalty"] = penalty

    result = {
        "projected": True,
        "actor_id": actor_id,
        "stats": stats,
        "equipped_items": equipped_items,
        "source": "deterministic_equipment_runtime",
    }

    inv["equipment_stats"] = result
    return result


def ammo_compatible_with_equipped_weapon(
    simulation_state: Dict[str, Any],
    *,
    actor_id: str = "player",
) -> Dict[str, Any]:
    inv = _inventory_for_actor(simulation_state, actor_id)
    items = [normalize_item_instance(_safe_dict(item)) for item in _safe_list(inv.get("items"))]
    equipment = _safe_dict(inv.get("equipment"))

    weapon_id = _safe_str(equipment.get("main_hand"))
    ammo_id = _safe_str(equipment.get("ammo"))

    weapon = _find_item(items, weapon_id)
    ammo = _find_item(items, ammo_id)

    if not weapon_id or not weapon:
        return {
            "compatible": True,
            "reason": "no_weapon_equipped",
            "source": "deterministic_equipment_runtime",
        }

    required_tag = _safe_str(_safe_dict(weapon.get("equipment")).get("requires_ammo_tag"))
    if not required_tag:
        return {
            "compatible": True,
            "reason": "weapon_does_not_require_ammo",
            "source": "deterministic_equipment_runtime",
        }

    if not ammo_id or not ammo:
        return {
            "compatible": False,
            "reason": "required_ammo_not_equipped",
            "required_ammo_tag": required_tag,
            "source": "deterministic_equipment_runtime",
        }

    compatible = required_tag in _item_tags(ammo)
    return {
        "compatible": compatible,
        "reason": "ammo_compatible" if compatible else "ammo_tag_mismatch",
        "weapon_item_id": weapon_id,
        "ammo_item_id": ammo_id,
        "required_ammo_tag": required_tag,
        "ammo_tags": sorted(_item_tags(ammo)),
        "source": "deterministic_equipment_runtime",
    }


def consume_equipped_ammo(
    simulation_state: Dict[str, Any],
    *,
    actor_id: str = "player",
    quantity: int = 1,
    tick: int = 0,
) -> Dict[str, Any]:
    inv = _inventory_for_actor(simulation_state, actor_id)
    if not inv:
        return {
            "consumed": False,
            "reason": "inventory_not_available",
            "actor_id": actor_id,
            "source": "deterministic_equipment_runtime",
        }

    compatibility = ammo_compatible_with_equipped_weapon(simulation_state, actor_id=actor_id)
    if compatibility.get("compatible") is not True:
        return {
            "consumed": False,
            "reason": _safe_str(compatibility.get("reason")),
            "compatibility": compatibility,
            "actor_id": actor_id,
            "source": "deterministic_equipment_runtime",
        }

    equipment = _safe_dict(inv.get("equipment"))
    ammo_id = _safe_str(equipment.get("ammo"))
    if not ammo_id:
        return {
            "consumed": False,
            "reason": "ammo_not_equipped",
            "actor_id": actor_id,
            "source": "deterministic_equipment_runtime",
        }

    items = [normalize_item_instance(_safe_dict(item)) for item in _safe_list(inv.get("items"))]
    ammo = _find_item(items, ammo_id)
    if not ammo:
        return {
            "consumed": False,
            "reason": "equipped_ammo_not_found",
            "actor_id": actor_id,
            "ammo_item_id": ammo_id,
            "source": "deterministic_equipment_runtime",
        }

    quantity = max(1, _safe_int(quantity, 1))
    before = max(0, _safe_int(ammo.get("quantity"), 0))
    if before < quantity:
        return {
            "consumed": False,
            "reason": "insufficient_ammo",
            "actor_id": actor_id,
            "ammo_item_id": ammo_id,
            "quantity_before": before,
            "quantity_required": quantity,
            "source": "deterministic_equipment_runtime",
        }

    remove_result = remove_quantity_from_items_list(
        items,
        item_id=ammo_id,
        quantity=quantity,
    )
    if not remove_result.get("removed_all"):
        return {
            "consumed": False,
            "reason": "failed_to_consume_ammo",
            "actor_id": actor_id,
            "ammo_item_id": ammo_id,
            "source": "deterministic_equipment_runtime",
        }

    inv["items"] = _safe_list(remove_result.get("items"))
    inv = recalculate_inventory_derived_fields(inv)

    after = before - quantity
    if after <= 0:
        equipment.pop("ammo", None)
    inv["equipment"] = equipment

    if actor_id in {"", "player"}:
        simulation_state["player_state"]["inventory"] = inv

    return {
        "consumed": True,
        "actor_id": actor_id,
        "ammo_item_id": ammo_id,
        "quantity_before": before,
        "quantity_after": after,
        "quantity_consumed": quantity,
        "equipment": deepcopy(equipment),
        "carry_weight": inv.get("carry_weight"),
        "encumbrance_state": inv.get("encumbrance_state"),
        "tick": int(tick or 0),
        "source": "deterministic_equipment_runtime",
    }
