"""Deterministic recipe crafting helpers for RPG sessions.

The engine owns recipe requirements, ingredient matching, output mechanics, and
inventory mutation. AI/display systems may decorate names later, but cannot
change compatibility, quantities, station rules, or outputs here.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.inventory_items import (
    consume_inventory_item,
    display_item_name,
    inventory_quantity,
    is_protected_item,
    merge_inventory_stack,
    normalize_inventory_items,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

CRAFTING_RECIPES: dict[str, dict[str, Any]] = {
    "torch": {
        "recipe_id": "torch",
        "name": "Craft Torch",
        "station": "campfire",
        "requirements": [
            {"kind": "property", "property": "burnable", "quantity": 1},
            {"kind": "material", "material_id": "cloth", "quantity": 1},
            {"kind": "material", "material_id": "lamp_oil", "quantity": 1},
        ],
        "output": {
            "item_id": "torch",
            "id": "torch",
            "name": "Torch",
            "item_type": "tool",
            "type": "tool",
            "quantity": 1,
            "stackable": False,
            "capabilities": [{"capability_id": "light_scene", "kind": "tool_use"}],
            "value": {"copper": 4},
        },
    },
    "crude_blade": {
        "recipe_id": "crude_blade",
        "name": "Craft Crude Blade",
        "station": "forge",
        "requirements": [
            {"kind": "role", "role": "metal", "quantity": 2},
            {"kind": "property", "property": "binding", "quantity": 1},
        ],
        "output": {
            "item_id": "crude_blade",
            "id": "crude_blade",
            "name": "Crude Blade",
            "item_type": "weapon",
            "type": "weapon",
            "weapon_type": "dagger",
            "quantity": 1,
            "stackable": False,
            "damage": {"slashing": 4},
            "value": {"copper": 18},
        },
    },
}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _slug(value: Any, fallback: str = "item") -> str:
    raw = _norm(value or fallback)
    slug = "".join(char if char.isalnum() else "_" for char in raw).strip("_")
    return slug or fallback


def get_recipe(recipe_id: str | None) -> dict[str, Any] | None:
    key = _slug(recipe_id or "")
    recipe = CRAFTING_RECIPES.get(key)
    return deepcopy(recipe) if recipe else None


def _item_properties(item: dict[str, Any]) -> set[str]:
    return {_norm(prop) for prop in _safe_list(item.get("properties")) if _text(prop)}


def _item_role(item: dict[str, Any]) -> str:
    return _norm(item.get("material_role") or item.get("role"))


def _matches_requirement(item: dict[str, Any], requirement: dict[str, Any]) -> bool:
    if is_protected_item(item):
        return False
    kind = _norm(requirement.get("kind"))
    if kind in {"item", "exact"}:
        wanted = _norm(requirement.get("item_id") or requirement.get("id"))
        return wanted in {_norm(item.get("item_id")), _norm(item.get("id"))}
    if kind == "material":
        return _norm(item.get("material_id")) == _norm(requirement.get("material_id"))
    if kind == "role":
        return _item_role(item) == _norm(requirement.get("role"))
    if kind == "property":
        return _norm(requirement.get("property")) in _item_properties(item)
    return False


def _requirement_label(requirement: dict[str, Any]) -> str:
    kind = _norm(requirement.get("kind"))
    if kind == "material":
        return _text(requirement.get("material_id"), "material")
    if kind == "role":
        return f"{_text(requirement.get('role'), 'material')} role"
    if kind == "property":
        return f"{_text(requirement.get('property'), 'property')} property material"
    return _text(requirement.get("item_id") or requirement.get("id"), "item")


def _consumed_requirement_label(requirement: dict[str, Any]) -> str:
    kind = _norm(requirement.get("kind"))
    if kind == "property":
        return f"{_text(requirement.get('property'), 'property')} material"
    return _requirement_label(requirement)


def _find_consumptions(inventory: list[dict[str, Any]], requirements: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    remaining = {index: inventory_quantity(item) for index, item in enumerate(inventory)}
    consumptions: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for requirement in requirements:
        needed = max(1, int(requirement.get("quantity") or 1))
        consumed_for_requirement: list[dict[str, Any]] = []
        for index, item in enumerate(inventory):
            if needed <= 0:
                break
            available = remaining.get(index, 0)
            if available <= 0 or not _matches_requirement(item, requirement):
                continue
            amount = min(available, needed)
            remaining[index] = available - amount
            needed -= amount
            consumed_for_requirement.append(
                {
                    "index": index,
                    "quantity": amount,
                    "item_id": item.get("item_id") or item.get("id"),
                    "material_id": item.get("material_id"),
                    "name": display_item_name(item),
                    "requirement": _consumed_requirement_label(requirement),
                }
            )
        if needed > 0:
            missing.append({"requirement": _requirement_label(requirement), "quantity": needed})
        consumptions.extend(consumed_for_requirement)
    return consumptions, missing


def _build_output(recipe: dict[str, Any], *, quality: str = "standard") -> dict[str, Any]:
    output = deepcopy(_safe_dict(recipe.get("output")))
    output.setdefault("quantity", 1)
    output.setdefault("item_id", _slug(output.get("id") or output.get("name") or recipe.get("recipe_id")))
    output.setdefault("id", output["item_id"])
    output.setdefault("name", _text(output.get("name"), _text(recipe.get("name"), "Crafted Item")))
    output.setdefault("item_type", output.get("type") or "crafted_item")
    output.setdefault("type", output["item_type"])
    output.setdefault("quality", quality)
    output.setdefault("source_history", [])
    output["source_history"] = [
        {"source": "recipe_craft", "recipe_id": recipe.get("recipe_id"), "recipe_name": recipe.get("name")},
        *_safe_list(output.get("source_history")),
    ][:20]
    if not output.get("stackable"):
        output["instance_id"] = f"inst_{_slug(output.get('item_id'))}_crafted"
    return output


def preview_craft(inventory: list[dict[str, Any]], recipe_id: str | None, *, station: str | None = None) -> dict[str, Any]:
    normalized, _trace = normalize_inventory_items(inventory)
    recipe = get_recipe(recipe_id)
    if not recipe:
        return {"ok": False, "error": "recipe_not_found", "recipe_id": recipe_id}
    required_station = _norm(recipe.get("station"))
    provided_station = _norm(station)
    if required_station and provided_station != required_station:
        return {
            "ok": False,
            "error": "wrong_station",
            "recipe_id": recipe.get("recipe_id"),
            "required_station": recipe.get("station"),
            "station": station,
        }
    consumptions, missing = _find_consumptions(normalized, _safe_list(recipe.get("requirements")))
    output = _build_output(recipe)
    if missing:
        return {
            "ok": False,
            "error": "missing_ingredients",
            "recipe_id": recipe.get("recipe_id"),
            "recipe_name": recipe.get("name"),
            "missing": missing,
            "consumptions": consumptions,
            "output_preview": output,
        }
    return {
        "ok": True,
        "recipe_id": recipe.get("recipe_id"),
        "recipe_name": recipe.get("name"),
        "station": recipe.get("station"),
        "consumptions": consumptions,
        "output_preview": output,
    }


def craft_from_inventory(inventory: list[dict[str, Any]], recipe_id: str | None, *, station: str | None = None) -> dict[str, Any]:
    normalized, _trace = normalize_inventory_items(inventory)
    inventory[:] = normalized
    preview = preview_craft(inventory, recipe_id, station=station)
    if not preview.get("ok"):
        return preview

    consumptions = _safe_list(preview.get("consumptions"))
    for entry in sorted(consumptions, key=lambda value: int(value.get("index") or 0), reverse=True):
        consume_inventory_item(inventory, int(entry.get("index") or 0), int(entry.get("quantity") or 1))

    output = deepcopy(_safe_dict(preview.get("output_preview")))
    added = merge_inventory_stack(inventory, output)
    trace = {
        "event": "item_crafted",
        "recipe_id": preview.get("recipe_id"),
        "recipe_name": preview.get("recipe_name"),
        "station": preview.get("station"),
        "consumed_items": [
            {key: value for key, value in entry.items() if key != "index"}
            for entry in consumptions
        ],
        "output": output,
        "mechanics_source": "engine_crafting_v1",
    }
    return {
        "ok": True,
        "recipe_id": preview.get("recipe_id"),
        "recipe_name": preview.get("recipe_name"),
        "station": preview.get("station"),
        "consumed_items": trace["consumed_items"],
        "output": added,
        "trace": trace,
        "detail": f"Crafted {display_item_name(added)} using {preview.get('recipe_name')}.",
    }
