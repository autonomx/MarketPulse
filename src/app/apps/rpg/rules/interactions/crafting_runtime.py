from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.interactions.item_model import (
    add_item_to_items_list,
    normalize_item_instance,
    recalculate_inventory_derived_fields,
    remove_quantity_from_items_list,
)
from app.apps.rpg.rules.interactions.recipe_catalog import find_recipe_by_name, get_recipe
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _inventory(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _safe_dict(simulation_state.get("player_state"))
    inv = _safe_dict(player_state.get("inventory"))
    if not isinstance(inv.get("items"), list):
        inv["items"] = []
    if not isinstance(inv.get("equipment"), dict):
        inv["equipment"] = {}
    player_state["inventory"] = inv
    simulation_state["player_state"] = player_state
    return inv


def _quantity_by_definition(items: List[Dict[str, Any]]) -> Dict[str, int]:
    quantities: Dict[str, int] = {}
    for item in _safe_list(items):
        normalized = normalize_item_instance(_safe_dict(item))
        definition_id = _safe_str(normalized.get("definition_id"))
        if not definition_id:
            continue
        quantities[definition_id] = quantities.get(definition_id, 0) + _safe_int(normalized.get("quantity"), 1)
    return quantities


def _result(
    *,
    resolved: bool,
    changed_state: bool,
    reason: str,
    action: Dict[str, Any],
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = {
        "resolved": resolved,
        "changed_state": changed_state,
        "reason": reason,
        "semantic_action_v2": deepcopy(_safe_dict(action)),
        "source": "deterministic_crafting_runtime",
    }
    payload.update(_safe_dict(extra or {}))
    return payload


def apply_crafting_interaction(
    simulation_state: Dict[str, Any],
    *,
    semantic_action_v2: Dict[str, Any],
    tick: int = 0,
) -> Dict[str, Any]:
    action = _safe_dict(semantic_action_v2)
    if _safe_str(action.get("kind")) != "craft":
        return _result(
            resolved=False,
            changed_state=False,
            reason="not_crafting_action",
            action=action,
        )

    recipe_ref = _safe_str(action.get("target_ref") or action.get("recipe_ref"))
    recipe_id = _safe_str(action.get("recipe_id"))

    recipe = get_recipe(recipe_id) if recipe_id else find_recipe_by_name(recipe_ref)
    if not recipe:
        return _result(
            resolved=False,
            changed_state=False,
            reason="recipe_not_found",
            action=action,
            extra={"recipe_ref": recipe_ref},
        )

    inv = _inventory(simulation_state)
    items = [normalize_item_instance(_safe_dict(item)) for item in _safe_list(inv.get("items"))]
    quantities = _quantity_by_definition(items)

    missing = []
    for requirement in _safe_list(recipe.get("requires")):
        definition_id = _safe_str(_safe_dict(requirement).get("definition_id"))
        required_qty = max(1, _safe_int(_safe_dict(requirement).get("quantity"), 1))
        available_qty = quantities.get(definition_id, 0)
        if available_qty < required_qty:
            missing.append({
                "definition_id": definition_id,
                "required_quantity": required_qty,
                "available_quantity": available_qty,
            })

    if missing:
        return _result(
            resolved=False,
            changed_state=False,
            reason="missing_crafting_materials",
            action=action,
            extra={
                "recipe_id": _safe_str(recipe.get("recipe_id")),
                "missing_materials": missing,
            },
        )

    consumed = []
    updated_items = items

    for requirement in _safe_list(recipe.get("requires")):
        definition_id = _safe_str(_safe_dict(requirement).get("definition_id"))
        required_qty = max(1, _safe_int(_safe_dict(requirement).get("quantity"), 1))
        remove_result = remove_quantity_from_items_list(
            updated_items,
            definition_id=definition_id,
            quantity=required_qty,
        )
        if not remove_result.get("removed_all"):
            return _result(
                resolved=False,
                changed_state=False,
                reason="failed_to_consume_crafting_materials",
                action=action,
                extra={
                    "recipe_id": _safe_str(recipe.get("recipe_id")),
                    "definition_id": definition_id,
                },
            )

        updated_items = _safe_list(remove_result.get("items"))
        consumed.append({
            "definition_id": definition_id,
            "quantity": required_qty,
        })

    created = []
    add_result: Dict[str, Any] = {"items": updated_items}

    for output in _safe_list(recipe.get("produces")):
        definition_id = _safe_str(_safe_dict(output).get("definition_id"))
        quantity = max(1, _safe_int(_safe_dict(output).get("quantity"), 1))
        item = normalize_item_instance({
            "definition_id": definition_id,
            "quantity": quantity,
            "source": "deterministic_crafting_runtime",
        })
        add_result = add_item_to_items_list(_safe_list(add_result.get("items")), item)
        created.append({
            "definition_id": definition_id,
            "quantity": quantity,
            "item": deepcopy(item),
        })

    inv["items"] = _safe_list(add_result.get("items"))
    inv = recalculate_inventory_derived_fields(inv)
    simulation_state["player_state"]["inventory"] = inv

    return _result(
        resolved=True,
        changed_state=True,
        reason="recipe_crafted",
        action=action,
        extra={
            "recipe_id": _safe_str(recipe.get("recipe_id")),
            "recipe_name": _safe_str(recipe.get("name")),
            "materials_consumed": consumed,
            "items_created": created,
            "stacked": bool(add_result.get("stacked")),
            "stack_events": deepcopy(_safe_list(add_result.get("stack_events"))),
            "carry_weight": inv.get("carry_weight"),
            "encumbrance_state": inv.get("encumbrance_state"),
            "tick": int(tick or 0),
        },
    )
