"""Deterministic item action summaries for RPG UI and autoplay surfaces."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.crafting import CRAFTING_RECIPES, preview_craft
from app.apps.rpg.session.equipment import resolve_equipment_slot
from app.apps.rpg.session.inventory_items import display_item_name, inventory_quantity, is_protected_item, item_type, normalize_inventory_items
from app.apps.rpg.session.item_materials import salvage_item
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _player(state: dict[str, Any]) -> dict[str, Any]:
    return _safe_dict(_safe_dict(state).get("player"))


def _known_recipe_ids(state: dict[str, Any]) -> list[str]:
    crafting = _safe_dict(_safe_dict(state).get("crafting"))
    known = crafting.get("known_recipes") or crafting.get("known_recipe_ids") or []
    if isinstance(known, dict):
        ids = [str(key) for key, value in known.items() if value]
    else:
        ids = [str(value) for value in _safe_list(known)]
    return sorted({recipe_id for recipe_id in ids if recipe_id})


def _has_display_use(item: dict[str, Any]) -> bool:
    kind = item_type(item)
    tags = {_norm(tag) for tag in _safe_list(item.get("tags")) if _text(tag)}
    if kind in {"consumable", "tool", "document", "map", "note", "quest_item"}:
        return True
    if item.get("effects") or item.get("use_ops") or item.get("capabilities") or item.get("signals"):
        return True
    return bool(tags.intersection({"usable", "map", "ledger", "blueprint", "seal", "letter"}))


def _has_equipment_use(item: dict[str, Any]) -> bool:
    kind = item_type(item)
    if kind in {"weapon", "armor", "shield", "tool", "equipment"}:
        return True
    return bool(item.get("damage") or item.get("defense") or item.get("resistances") or item.get("stats") or item.get("modifiers"))


def _has_value(item: dict[str, Any]) -> bool:
    value = item.get("value") or item.get("price") or item.get("base_value")
    return bool(value)


def summarize_item_actions(item: dict[str, Any], *, genre: str = "classic_fantasy") -> dict[str, Any]:
    """Return deterministic actions available for a normalized inventory item."""
    normalized, _trace = normalize_inventory_items([item])
    current = normalized[0] if normalized else deepcopy(item)
    protected = is_protected_item(current)
    actions: list[dict[str, Any]] = [{"action": "inspect", "enabled": True, "reason": "inventory_item"}]

    if _has_display_use(current):
        actions.append({"action": "use", "enabled": True, "reason": "usable_item"})
    if _has_equipment_use(current):
        actions.append({"action": "equip", "enabled": True, "slot": resolve_equipment_slot(current), "reason": "equipment_item"})

    salvage = salvage_item(current, genre=genre)
    actions.append(
        {
            "action": "salvage",
            "enabled": bool(salvage.ok),
            "reason": "salvageable" if salvage.ok else salvage.error or "not_salvageable",
            "outputs_preview": salvage.outputs if salvage.ok else [],
        }
    )

    actions.append({"action": "drop", "enabled": not protected, "reason": "protected_item" if protected else "droppable"})
    actions.append({"action": "sell", "enabled": bool(_has_value(current) and not protected), "reason": "protected_item" if protected else "has_value" if _has_value(current) else "no_value"})

    return {
        "item_id": current.get("item_id") or current.get("id"),
        "instance_id": current.get("instance_id"),
        "name": display_item_name(current),
        "quantity": inventory_quantity(current),
        "item_type": item_type(current),
        "protected": protected,
        "actions": actions,
    }


def summarize_recipe_actions(state: dict[str, Any], *, station: str | None = None) -> list[dict[str, Any]]:
    player = _player(state)
    inventory, _trace = normalize_inventory_items(_safe_list(player.get("inventory")))
    known = _known_recipe_ids(state)
    recipe_ids = known or sorted(CRAFTING_RECIPES.keys())
    summaries: list[dict[str, Any]] = []
    for recipe_id in recipe_ids:
        preview = preview_craft(inventory, recipe_id, station=station)
        recipe = CRAFTING_RECIPES.get(recipe_id, {})
        summaries.append(
            {
                "action": "craft",
                "recipe_id": recipe_id,
                "recipe_name": preview.get("recipe_name") or recipe.get("name") or recipe_id,
                "enabled": bool(preview.get("ok")),
                "error": preview.get("error"),
                "missing": preview.get("missing", []),
                "station": preview.get("station") or station,
                "required_station": preview.get("required_station") or recipe.get("station"),
                "output_preview": preview.get("output_preview"),
            }
        )
    return summaries


def build_item_action_summary(state: dict[str, Any], *, station: str | None = None, genre: str = "classic_fantasy") -> dict[str, Any]:
    """Build an AI-free item action surface for UI, report, and autoplay consumers."""
    player = _player(state)
    inventory, inventory_trace = normalize_inventory_items(_safe_list(player.get("inventory")))
    item_actions = [summarize_item_actions(item, genre=genre) for item in inventory]
    recipe_actions = summarize_recipe_actions({**_safe_dict(state), "player": {**player, "inventory": inventory}}, station=station)
    enabled_count = sum(1 for item in item_actions for action in item.get("actions", []) if action.get("enabled")) + sum(1 for action in recipe_actions if action.get("enabled"))
    trace = {
        "event": "item_action_summary_built",
        "inventory_count": len(inventory),
        "recipe_count": len(recipe_actions),
        "enabled_action_count": enabled_count,
        "inventory_changed": bool(inventory_trace.get("changed")),
        "mechanics_source": "engine_item_action_summary_v1",
    }
    return {
        "inventory_actions": item_actions,
        "recipe_actions": recipe_actions,
        "enabled_action_count": enabled_count,
        "trace": trace,
        "mechanics_source": "engine_item_action_summary_v1",
    }
