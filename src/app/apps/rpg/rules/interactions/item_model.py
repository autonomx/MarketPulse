from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.interactions.item_catalog import (
    definition_for_item_like,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

DEFAULT_CARRY_CAPACITY = 50.0


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _slug(value: str) -> str:
    value = _safe_str(value).strip().lower()
    value = re.sub(r"[^a-z0-9:_-]+", "_", value)
    value = value.strip("_") or "unknown"
    return value


def item_id_for_definition(definition_id: str) -> str:
    clean = _safe_str(definition_id).replace("def:", "")
    return f"item:{_slug(clean)}"


def normalize_currency_value(value: Any) -> Dict[str, int]:
    value = _safe_dict(value)
    return {
        "gold": max(0, _safe_int(value.get("gold"), 0)),
        "silver": max(0, _safe_int(value.get("silver"), 0)),
        "copper": max(0, _safe_int(value.get("copper"), 0)),
    }


def normalize_item_instance(item: Dict[str, Any]) -> Dict[str, Any]:
    item = deepcopy(_safe_dict(item))
    definition = definition_for_item_like(item)

    definition_id = (
        _safe_str(item.get("definition_id"))
        or _safe_str(definition.get("definition_id"))
    )

    name = (
        _safe_str(item.get("name"))
        or _safe_str(definition.get("name"))
        or definition_id.replace("def:", "").replace("_", " ")
        or _safe_str(item.get("item_id")).replace("item:", "").replace("_", " ")
        or "unknown item"
    )

    item_id = (
        _safe_str(item.get("item_id"))
        or _safe_str(item.get("id"))
        or _safe_str(item.get("entity_id"))
        or item_id_for_definition(definition_id)
    )

    stackable = bool(item.get("stackable", definition.get("stackable", False)))
    max_stack = max(1, _safe_int(item.get("max_stack", definition.get("max_stack", 99 if stackable else 1)), 1))
    quantity = max(1, _safe_int(item.get("quantity"), 1))
    if not stackable:
        quantity = 1
        max_stack = 1

    unit_weight = max(0.0, _safe_float(item.get("unit_weight", definition.get("unit_weight", 0.0)), 0.0))
    total_weight = round(unit_weight * quantity, 4)

    normalized = {
        "item_id": item_id,
        "definition_id": definition_id or item_id.replace("item:", "def:"),
        "name": name,
        "kind": _safe_str(item.get("kind") or item.get("item_type") or item.get("type") or definition.get("kind")),
        "quantity": quantity,
        "stackable": stackable,
        "max_stack": max_stack,
        "unit_weight": unit_weight,
        "total_weight": total_weight,
        "rarity": _safe_str(item.get("rarity") or definition.get("rarity") or "common"),
        "value": normalize_currency_value(item.get("value") or definition.get("value")),
        "tags": _safe_list(item.get("tags") or definition.get("tags")),
        "aliases": _safe_list(item.get("aliases")),
        "source": _safe_str(item.get("source") or "deterministic_item_model"),
    }

    if "location_id" in item:
        normalized["location_id"] = _safe_str(item.get("location_id"))

    equipment = _safe_dict(item.get("equipment") or definition.get("equipment"))
    if equipment:
        normalized["equipment"] = deepcopy(equipment)
        if _safe_str(equipment.get("slot")):
            normalized["slot"] = _safe_str(equipment.get("slot"))

    if _safe_str(item.get("slot")):
        normalized["slot"] = _safe_str(item.get("slot"))

    condition = _safe_dict(item.get("condition") or definition.get("condition"))
    if condition:
        normalized["condition"] = {
            "durability": max(0.0, min(1.0, _safe_float(condition.get("durability"), 1.0))),
            "max_durability": max(0.0, _safe_float(condition.get("max_durability"), 1.0)),
        }

    container = _safe_dict(item.get("container") or definition.get("container"))
    if container:
        capacity = _safe_float(container.get("capacity_weight"), 0.0)
        contained_items = [
            normalize_item_instance(_safe_dict(contained))
            for contained in _safe_list(container.get("items"))
        ]
        normalized["container"] = {
            "capacity_weight": capacity,
            "items": contained_items,
        }

    repair = _safe_dict(item.get("repair") or definition.get("repair"))
    if repair:
        normalized["repair"] = deepcopy(repair)

    # Future item-system metadata. Preserve these now so later bundles do not
    # hit the same normalization-loss bug that repair metadata hit.
    consumable = _safe_dict(item.get("consumable") or definition.get("consumable"))
    if consumable:
        normalized["consumable"] = deepcopy(consumable)

    ammo = _safe_dict(item.get("ammo") or definition.get("ammo"))
    if ammo:
        normalized["ammo"] = deepcopy(ammo)

    crafting = _safe_dict(item.get("crafting") or definition.get("crafting"))
    if crafting:
        normalized["crafting"] = deepcopy(crafting)

    # Preserve lightweight custom state without allowing it to override normalized fields.
    for key in ("state", "metadata", "owner_id"):
        if key in item:
            normalized[key] = deepcopy(item[key])

    return normalized


def can_stack_items(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    a = normalize_item_instance(a)
    b = normalize_item_instance(b)

    return (
        bool(a.get("stackable"))
        and bool(b.get("stackable"))
        and _safe_str(a.get("definition_id")) == _safe_str(b.get("definition_id"))
        and _safe_str(a.get("rarity")) == _safe_str(b.get("rarity"))
    )


def add_item_to_items_list(items: List[Any], item: Dict[str, Any]) -> Dict[str, Any]:
    """Add item, stacking when possible.

    Returns a result with updated items and stack/add details.
    """
    normalized_items = [normalize_item_instance(_safe_dict(existing)) for existing in _safe_list(items)]
    incoming = normalize_item_instance(item)
    remaining_qty = _safe_int(incoming.get("quantity"), 1)
    stack_events = []

    if incoming.get("stackable"):
        for existing in normalized_items:
            if remaining_qty <= 0:
                break
            if not can_stack_items(existing, incoming):
                continue

            max_stack = _safe_int(existing.get("max_stack"), 1)
            current_qty = _safe_int(existing.get("quantity"), 1)
            room = max(0, max_stack - current_qty)
            if room <= 0:
                continue

            moved = min(room, remaining_qty)
            existing["quantity"] = current_qty + moved
            existing["total_weight"] = round(_safe_float(existing.get("unit_weight"), 0.0) * _safe_int(existing.get("quantity"), 1), 4)
            remaining_qty -= moved
            stack_events.append({
                "target_item_id": _safe_str(existing.get("item_id")),
                "quantity_added": moved,
                "quantity_after": existing["quantity"],
            })

    added_items: list[Any] = []
    while remaining_qty > 0:
        new_item = deepcopy(incoming)
        new_qty = min(remaining_qty, _safe_int(new_item.get("max_stack"), 1))
        new_item["quantity"] = new_qty
        new_item["total_weight"] = round(_safe_float(new_item.get("unit_weight"), 0.0) * new_qty, 4)

        # Avoid duplicate IDs for overflow stacks.
        if any(_safe_str(existing.get("item_id")) == _safe_str(new_item.get("item_id")) for existing in normalized_items):
            suffix = len(normalized_items) + len(added_items) + 1
            new_item["item_id"] = f"{_safe_str(new_item.get('item_id'))}:{suffix}"

        normalized_items.append(new_item)
        added_items.append(deepcopy(new_item))
        remaining_qty -= new_qty

    return {
        "items": normalized_items,
        "stacked": bool(stack_events),
        "stack_events": stack_events,
        "added_items": added_items,
        "incoming_item": incoming,
        "source": "deterministic_item_model",
    }


def remove_quantity_from_items_list(
    items: List[Any],
    *,
    item_id: str = "",
    definition_id: str = "",
    quantity: int = 1,
) -> Dict[str, Any]:
    normalized_items = [normalize_item_instance(_safe_dict(item)) for item in _safe_list(items)]
    quantity = max(1, _safe_int(quantity, 1))
    removed = []
    remaining_to_remove = quantity

    for index, item in list(enumerate(normalized_items)):
        if remaining_to_remove <= 0:
            break

        matches_id = item_id and _safe_str(item.get("item_id")) == _safe_str(item_id)
        matches_def = definition_id and _safe_str(item.get("definition_id")) == _safe_str(definition_id)
        if not matches_id and not matches_def:
            continue

        available = _safe_int(item.get("quantity"), 1)
        take = min(available, remaining_to_remove)

        removed_item = deepcopy(item)
        removed_item["quantity"] = take
        removed_item["total_weight"] = round(_safe_float(removed_item.get("unit_weight"), 0.0) * take, 4)
        removed.append(removed_item)

        remaining_qty = available - take
        if remaining_qty <= 0:
            normalized_items.pop(index)
        else:
            normalized_items[index]["quantity"] = remaining_qty
            normalized_items[index]["total_weight"] = round(
                _safe_float(normalized_items[index].get("unit_weight"), 0.0) * remaining_qty,
                4,
            )

        remaining_to_remove -= take

    return {
        "removed_all": remaining_to_remove == 0,
        "removed_items": removed,
        "items": normalized_items,
        "quantity_removed": quantity - remaining_to_remove,
        "quantity_missing": remaining_to_remove,
        "source": "deterministic_item_model",
    }


def calculate_item_total_weight(item: Dict[str, Any]) -> float:
    normalized = normalize_item_instance(item)
    own_weight = _safe_float(normalized.get("total_weight"), 0.0)

    container = _safe_dict(normalized.get("container"))
    contained = _safe_list(container.get("items"))
    contained_weight = 0.0
    for child in contained:
        contained_weight += calculate_item_total_weight(_safe_dict(child))

    return round(own_weight + contained_weight, 4)


def calculate_container_contents_weight(item: Dict[str, Any]) -> float:
    normalized = normalize_item_instance(item)
    container = _safe_dict(normalized.get("container"))
    total = 0.0
    for child in _safe_list(container.get("items")):
        total += calculate_item_total_weight(_safe_dict(child))
    return round(total, 4)


def calculate_inventory_weight(items: List[Any]) -> float:
    total = 0.0
    for item in _safe_list(items):
        total += calculate_item_total_weight(_safe_dict(item))
    return round(total, 4)


def encumbrance_state_for_weight(weight: float, capacity: float) -> str:
    capacity = max(0.01, _safe_float(capacity, DEFAULT_CARRY_CAPACITY))
    ratio = _safe_float(weight, 0.0) / capacity

    if ratio > 1.5:
        return "immobile"
    if ratio > 1.0:
        return "overloaded"
    if ratio > 0.7:
        return "burdened"
    return "normal"


def recalculate_inventory_derived_fields(inventory: Dict[str, Any]) -> Dict[str, Any]:
    inventory = deepcopy(_safe_dict(inventory))
    items = [normalize_item_instance(_safe_dict(item)) for item in _safe_list(inventory.get("items"))]
    inventory["items"] = items

    if not isinstance(inventory.get("equipment"), dict):
        inventory["equipment"] = {}

    capacity = _safe_float(inventory.get("carry_capacity"), DEFAULT_CARRY_CAPACITY)
    weight = calculate_inventory_weight(items)

    inventory["carry_capacity"] = capacity
    inventory["carry_weight"] = weight
    inventory["encumbrance_state"] = encumbrance_state_for_weight(weight, capacity)
    inventory["source"] = _safe_str(inventory.get("source") or "deterministic_item_model")
    return inventory
