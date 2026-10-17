"""Deterministic inventory item normalization for RPG sessions.

This module is intentionally AI-free: it normalizes legacy saves and inventory
stacks into engine-owned item instance shapes without inventing mechanics.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

STACKABLE_ITEM_TYPES = {
    "ammo",
    "consumable",
    "crafting_material",
    "currency",
    "material",
    "supply",
}

PROTECTED_ITEM_TYPES = {"quest", "quest_item"}
PROTECTED_NAMES = {"journal", "quest journal"}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _slug(value: Any, fallback: str = "item") -> str:
    raw = _norm(value or fallback)
    slug = "".join(char if char.isalnum() else "_" for char in raw).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or fallback


def inventory_quantity(item: dict[str, Any]) -> int:
    for key in ("quantity", "count", "qty", "amount"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return max(0, int(value))
    return 1


def display_item_name(item: dict[str, Any]) -> str:
    display = _safe_dict(item.get("display"))
    return _text(item.get("name") or item.get("label") or item.get("display_name") or display.get("name") or item.get("id") or item.get("item_id"), "Item")


def canonical_item_id(item: dict[str, Any]) -> str:
    if _text(item.get("item_id")):
        return _slug(item.get("item_id"))
    if _text(item.get("id")):
        return _slug(item.get("id"))
    if _text(item.get("material_id")):
        return _slug(item.get("material_id"))
    return _slug(display_item_name(item))


def item_type(item: dict[str, Any]) -> str:
    explicit = _norm(item.get("item_type") or item.get("type") or item.get("category"))
    if explicit:
        return explicit
    if _text(item.get("material_id")):
        return "crafting_material"
    name = _norm(display_item_name(item))
    if name in PROTECTED_NAMES:
        return "quest_item"
    if any(token in name for token in ("potion", "tonic", "ration", "food")):
        return "consumable"
    return "misc"


def is_protected_item(item: dict[str, Any]) -> bool:
    tags = {_norm(tag) for tag in _safe_list(item.get("tags")) if _text(tag)}
    if item.get("protected") is True or "protected" in tags:
        return True
    if item_type(item) in PROTECTED_ITEM_TYPES:
        return True
    return _norm(display_item_name(item)) in PROTECTED_NAMES


def is_stackable_item(item: dict[str, Any]) -> bool:
    if item.get("stackable") is True:
        return True
    if _text(item.get("material_id")):
        return True
    return item_type(item) in STACKABLE_ITEM_TYPES


def stack_key(item: dict[str, Any]) -> str:
    if not is_stackable_item(item):
        return ""
    if _text(item.get("material_id")):
        return f"material:{_slug(item.get('material_id'))}"
    return f"item:{canonical_item_id(item)}"


def _ensure_display(item: dict[str, Any], name: str) -> None:
    item.setdefault("name", name)
    display = _safe_dict(item.get("display"))
    display.setdefault("name", name)
    item["display"] = display


def _source_history(entry: dict[str, Any]) -> list[dict[str, Any]]:
    history = [deepcopy(_safe_dict(item)) for item in _safe_list(entry.get("source_history")) if _safe_dict(item)]
    if not history:
        history.append({"source": "legacy_inventory", "name": display_item_name(entry)})
    return history[:20]


def _normalize_entry(raw: Any, ordinal: int) -> dict[str, Any]:
    if isinstance(raw, dict):
        item = deepcopy(raw)
    else:
        name = _text(raw, "Item")
        item = {"name": name, "quantity": 1, "source_history": [{"source": "legacy_inventory_string", "name": name}]}

    name = display_item_name(item)
    item_id = canonical_item_id(item)
    normalized_type = item_type(item)

    item["item_id"] = item_id
    item.setdefault("id", item_id)
    item["quantity"] = max(1, inventory_quantity(item))
    item["item_type"] = normalized_type
    item.setdefault("type", normalized_type)
    _ensure_display(item, name)

    if is_protected_item(item):
        item["protected"] = True

    if is_stackable_item(item):
        item["stackable"] = True
        item.pop("instance_id", None)
    else:
        item.setdefault("instance_id", f"inst_{item_id}_{ordinal + 1}")
        item.setdefault("stackable", False)

    item["source_history"] = _source_history(item)
    return item


def merge_inventory_stack(inventory: list[dict[str, Any]], stack: dict[str, Any]) -> dict[str, Any]:
    incoming = _normalize_entry(stack, len(inventory))
    key = stack_key(incoming)
    if key:
        for existing in inventory:
            if stack_key(existing) != key:
                continue
            existing["quantity"] = inventory_quantity(existing) + inventory_quantity(incoming)
            for field in (
                "item_id",
                "id",
                "item_type",
                "type",
                "material_id",
                "material_role",
                "family",
                "rarity",
                "quality",
                "properties",
                "stackable",
                "usable_in_recipes",
                "mechanics_source",
            ):
                if field not in existing and field in incoming:
                    existing[field] = deepcopy(incoming[field])
            existing_history = _safe_list(existing.get("source_history"))
            existing["source_history"] = [*existing_history, *incoming.get("source_history", [])][:20]
            return existing
    inventory.append(incoming)
    return incoming


def normalize_inventory_items(items: list[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    legacy_count = 0
    merged_count = 0
    changed = False

    for ordinal, raw in enumerate(_safe_list(items)):
        if not isinstance(raw, dict):
            legacy_count += 1
            changed = True
        item = _normalize_entry(raw, ordinal)
        before_len = len(normalized)
        if is_stackable_item(item):
            merge_inventory_stack(normalized, item)
        else:
            normalized.append(item)
        if len(normalized) == before_len:
            merged_count += 1
            changed = True
        if isinstance(raw, dict):
            original = deepcopy(raw)
            if item != original:
                changed = True

    trace = {
        "event": "inventory_normalized",
        "mechanics_source": "engine_inventory_normalization_v1",
        "changed": changed,
        "input_count": len(_safe_list(items)),
        "output_count": len(normalized),
        "legacy_count": legacy_count,
        "merged_count": merged_count,
    }
    return normalized, trace


def normalize_player_inventory(player: dict[str, Any]) -> dict[str, Any]:
    normalized, trace = normalize_inventory_items(_safe_list(player.get("inventory")))
    player["inventory"] = normalized
    trace["inventory"] = normalized
    return trace


def find_inventory_item(player: dict[str, Any], item_name: str | None) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
    trace = normalize_player_inventory(player)
    inventory = trace["inventory"]
    wanted = _norm(item_name)
    if not wanted:
        return inventory, -1, None
    for index, item in enumerate(inventory):
        names = [item.get("name"), item.get("label"), item.get("display_name"), _safe_dict(item.get("display")).get("name"), item.get("id"), item.get("item_id"), item.get("instance_id")]
        if any(_norm(name) == wanted for name in names):
            return inventory, index, item
    for index, item in enumerate(inventory):
        names = [item.get("name"), item.get("label"), item.get("display_name"), _safe_dict(item.get("display")).get("name"), item.get("id"), item.get("item_id"), item.get("instance_id")]
        if any(wanted in _norm(name) for name in names):
            return inventory, index, item
    return inventory, -1, None


def set_inventory_quantity(inventory: list[dict[str, Any]], index: int, quantity: int) -> None:
    if quantity <= 0:
        inventory.pop(index)
        return
    inventory[index]["quantity"] = quantity


def consume_inventory_item(inventory: list[dict[str, Any]], index: int, amount: int = 1) -> None:
    set_inventory_quantity(inventory, index, inventory_quantity(inventory[index]) - amount)
