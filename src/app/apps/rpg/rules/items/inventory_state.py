"""Phase 9.0 — Inventory state management.

Provides deterministic, serialisable inventory state with bounded capacity
and stack merging.
"""
from __future__ import annotations

from typing import Any, Dict, List, cast

from app.apps.rpg.rules.economy.currency import normalize_currency

from .item_registry import get_item_definition
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str

_MAX_INVENTORY_SLOTS = 50
_MAX_LAST_LOOT = 10
_MAX_EQUIPMENT_SLOTS = 12
_MAX_CURRENCY_KEYS = 8


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _normalize_stack(item: Dict[str, Any]) -> Dict[str, Any]:
    """Return a clean, serialisable stack record."""
    item = _safe_dict(item)
    item_id = _safe_str(item.get("item_id"))
    qty = max(1, _safe_int(item.get("qty"), 1))
    item_def = get_item_definition(item_id)

    result = {
        "item_id": item_id,
        "qty": qty,
        "name": _safe_str(item.get("name") or item_def.get("name")),
        "category": _safe_str(item.get("category") or item_def.get("category")),
        "tags": [str(tag) for tag in _safe_list(item.get("tags") or item_def.get("tags"))[:8]],
        "description": _safe_str(item.get("description") or item_def.get("description", "")),
        "value": _safe_int(item.get("value") or item_def.get("value"), 0),
        "durability": _safe_int(item.get("durability") or item_def.get("durability"), 100),
    }
    # Preserve instance_id for non-stackable items
    if item.get("instance_id"):
        result["instance_id"] = _safe_str(item["instance_id"])
    # Preserve combat_stats if present
    for field in ("combat_stats", "equipment", "quality"):
        val = item.get(field) or item_def.get(field)
        if isinstance(val, dict):
            result[field] = dict(val)
    return result


def normalize_inventory_state(inventory_state: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitise and bound the full inventory state."""
    inventory_state = _safe_dict(inventory_state)

    items = []
    for item in _safe_list(inventory_state.get("items"))[:_MAX_INVENTORY_SLOTS]:
        if isinstance(item, dict) and item.get("item_id"):
            items.append(_normalize_stack(item))

    equipment_in = _safe_dict(inventory_state.get("equipment"))
    equipment_out: Dict[str, Dict[str, Any]] = {}
    for slot_name in sorted(equipment_in.keys())[:_MAX_EQUIPMENT_SLOTS]:
        slot_value = equipment_in.get(slot_name)
        if isinstance(slot_value, dict) and slot_value.get("item_id"):
            equipment_out[str(slot_name)] = _normalize_stack(slot_value)

    currency = normalize_currency(_safe_dict(inventory_state.get("currency")))

    last_loot = []
    for item in _safe_list(inventory_state.get("last_loot"))[:_MAX_LAST_LOOT]:
        if isinstance(item, dict) and item.get("item_id"):
            last_loot.append(_normalize_stack(item))

    return {
        "items": items,
        "equipment": equipment_out,
        "capacity": max(0, _safe_int(inventory_state.get("capacity"), _MAX_INVENTORY_SLOTS)),
        "currency": currency,
        "last_loot": last_loot,
    }


def ensure_inventory_state(player_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure *player_state* has a well-formed ``inventory_state`` subtree."""
    player_state = _safe_dict(player_state)
    inventory_state = normalize_inventory_state(_safe_dict(player_state.get("inventory_state")))
    if not inventory_state.get("capacity"):
        inventory_state["capacity"] = _MAX_INVENTORY_SLOTS
    player_state["inventory_state"] = inventory_state
    return player_state


def _merge_stack(items: List[Dict[str, Any]], incoming: Dict[str, Any], capacity: int) -> List[Dict[str, Any]]:
    """Merge *incoming* into *items*, stacking if possible, respecting *capacity*."""
    incoming = _normalize_stack(incoming)
    if not incoming.get("item_id"):
        return items

    item_def = get_item_definition(cast(Any, incoming.get("item_id")))
    if item_def.get("stackable"):
        for idx, existing in enumerate(items):
            if _safe_str(existing.get("item_id")) == incoming["item_id"]:
                merged = dict(existing)
                merged["qty"] = _safe_int(existing.get("qty"), 1) + _safe_int(incoming.get("qty"), 1)
                items[idx] = _normalize_stack(merged)
                return items

    if len(items) >= capacity:
        return items

    items.append(incoming)
    return items


def add_inventory_items(inventory_state: Dict[str, Any], incoming_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Add *incoming_items* to the inventory, merging stacks where possible."""
    inventory_state = normalize_inventory_state(inventory_state)
    capacity = max(0, _safe_int(inventory_state.get("capacity"), _MAX_INVENTORY_SLOTS))
    items = list(inventory_state.get("items") or [])

    for item in _safe_list(incoming_items):
        if isinstance(item, dict):
            items = _merge_stack(items, item, capacity)

    inventory_state["items"] = items[:capacity]
    return inventory_state


def remove_inventory_item(inventory_state: Dict[str, Any], item_id: str, qty: int = 1) -> Dict[str, Any]:
    """Remove up to *qty* of *item_id* from inventory."""
    inventory_state = normalize_inventory_state(inventory_state)
    item_id = _safe_str(item_id)
    qty = max(1, _safe_int(qty, 1))

    items_out: List[Dict[str, Any]] = []
    remaining_to_remove = qty

    for item in _safe_list(inventory_state.get("items")):
        current = _normalize_stack(item)
        if current.get("item_id") != item_id or remaining_to_remove <= 0:
            items_out.append(current)
            continue

        current_qty = _safe_int(current.get("qty"), 1)
        if current_qty > remaining_to_remove:
            current["qty"] = current_qty - remaining_to_remove
            items_out.append(current)
            remaining_to_remove = 0
        else:
            remaining_to_remove -= current_qty

    inventory_state["items"] = items_out
    return inventory_state


def record_inventory_loot(inventory_state: Dict[str, Any], loot_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Add *loot_items* and record them as ``last_loot``."""
    inventory_state = add_inventory_items(inventory_state, loot_items)
    inventory_state["last_loot"] = [
        _normalize_stack(item)
        for item in _safe_list(loot_items)[:_MAX_LAST_LOOT]
        if isinstance(item, dict) and item.get("item_id")
    ]
    return inventory_state


def build_inventory_summary(inventory_state: Dict[str, Any]) -> Dict[str, Any]:
    """Return a compact summary of the current inventory."""
    inventory_state = normalize_inventory_state(inventory_state)
    items = _safe_list(inventory_state.get("items"))
    total_item_kinds = len(items)
    total_item_qty = sum(max(0, _safe_int(item.get("qty"), 0)) for item in items if isinstance(item, dict))
    return {
        "slots_used": total_item_kinds,
        "capacity": max(0, _safe_int(inventory_state.get("capacity"), _MAX_INVENTORY_SLOTS)),
        "total_item_qty": total_item_qty,
        "last_loot_count": len(_safe_list(inventory_state.get("last_loot"))),
    }


def equip_inventory_item(inventory_state: Dict[str, Any], item_id: str, slot: str = "") -> Dict[str, Any]:
    """Equip an item from inventory into an equipment slot."""
    inventory_state = normalize_inventory_state(inventory_state)
    item_id = _safe_str(item_id)
    items = _safe_list(inventory_state.get("items"))
    found = None
    for item in items:
        if isinstance(item, dict) and _safe_str(item.get("item_id")) == item_id:
            found = dict(item)
            break
    if not found:
        return inventory_state
    equip_data = _safe_dict(found.get("equipment"))
    target_slot = _safe_str(slot) or _safe_str(equip_data.get("slot")) or "main_hand"
    equipment = dict(inventory_state.get("equipment") or {})
    equipment[target_slot] = _normalize_stack(found)
    inventory_state["equipment"] = equipment
    return inventory_state


def unequip_inventory_slot(inventory_state: Dict[str, Any], slot: str) -> Dict[str, Any]:
    """Remove equipped item from a slot."""
    inventory_state = normalize_inventory_state(inventory_state)
    slot = _safe_str(slot)
    equipment = dict(inventory_state.get("equipment") or {})
    if slot in equipment:
        del equipment[slot]
    inventory_state["equipment"] = equipment
    return inventory_state


def find_inventory_item(inventory_state: Dict[str, Any], item_id: str) -> Dict[str, Any]:
    """Find and return an item from inventory by item_id, or empty dict."""
    inventory_state = normalize_inventory_state(inventory_state)
    item_id = _safe_str(item_id)
    for item in _safe_list(inventory_state.get("items")):
        if isinstance(item, dict) and _safe_str(item.get("item_id")) == item_id:
            return _normalize_stack(item)
    return {}


def get_inventory_item_for_drop(inventory_state: Dict[str, Any], item_id: str) -> Dict[str, Any]:
    inventory_state = normalize_inventory_state(inventory_state)
    for item in _safe_list(inventory_state.get("items")):
        item = _safe_dict(item)
        if _safe_str(item.get("item_id")) == _safe_str(item_id):
            return dict(item)
    return {}


def get_equipped_weapon(inventory_state: Dict[str, Any]) -> Dict[str, Any]:
    """Return the equipped weapon from main_hand slot, or empty dict."""
    inventory_state = normalize_inventory_state(inventory_state)
    equipment = _safe_dict(inventory_state.get("equipment"))
    main_hand = _safe_dict(equipment.get("main_hand"))
    if main_hand.get("item_id"):
        return main_hand
    return {}


def get_equipped_armor(inventory_state: Dict[str, Any]) -> list:
    """Return list of equipped armor items from all armor slots."""
    inventory_state = normalize_inventory_state(inventory_state)
    equipment = _safe_dict(inventory_state.get("equipment"))
    armor_items = []
    for slot_name, slot_item in equipment.items():
        if isinstance(slot_item, dict) and slot_name != "main_hand":
            cs = _safe_dict(slot_item.get("combat_stats"))
            if _safe_int(cs.get("defense_bonus")) > 0 or _safe_int(cs.get("block_bonus")) > 0:
                armor_items.append(dict(slot_item))
    return armor_items