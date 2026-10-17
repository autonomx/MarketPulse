from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.rules.interactions.item_model import (
    add_item_to_items_list,
    calculate_container_contents_weight,
    calculate_item_total_weight,
    normalize_item_instance,
    recalculate_inventory_derived_fields,
    remove_quantity_from_items_list,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


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


def _player_inventory(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _safe_dict(simulation_state.get("player_state"))
    inventory = _safe_dict(player_state.get("inventory"))
    if not isinstance(inventory.get("items"), list):
        inventory["items"] = []
    if not isinstance(inventory.get("equipment"), dict):
        inventory["equipment"] = {}
    player_state["inventory"] = inventory
    simulation_state["player_state"] = player_state
    return inventory


def _find_item_index(items, item_id: str) -> int:
    for idx, item in enumerate(_safe_list(items)):
        item = _safe_dict(item)
        if _safe_str(item.get("item_id")) == item_id:
            return idx
    return -1


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
        "source": "deterministic_container_runtime",
    }
    payload.update(_safe_dict(extra or {}))
    return payload


def apply_container_interaction(
    simulation_state: Dict[str, Any],
    *,
    semantic_action_v2: Dict[str, Any],
    tick: int = 0,
) -> Dict[str, Any]:
    action = _safe_dict(semantic_action_v2)
    kind = _safe_str(action.get("kind"))
    if kind != "put":
        return _result(
            resolved=False,
            changed_state=False,
            reason="not_container_action",
            action=action,
        )

    item_id = _safe_str(action.get("target_id"))
    container_id = _safe_str(action.get("secondary_target_id"))
    quantity = max(1, _safe_int(action.get("quantity"), 1))

    if not item_id or not container_id:
        return _result(
            resolved=False,
            changed_state=False,
            reason="missing_item_or_container",
            action=action,
        )

    inventory = _player_inventory(simulation_state)
    items = [normalize_item_instance(_safe_dict(item)) for item in _safe_list(inventory.get("items"))]

    container_index = _find_item_index(items, container_id)
    if container_index < 0:
        return _result(
            resolved=False,
            changed_state=False,
            reason="container_not_in_inventory",
            action=action,
            extra={"item_id": item_id, "container_id": container_id},
        )

    container_item = normalize_item_instance(items[container_index])
    container = _safe_dict(container_item.get("container"))
    if not container:
        return _result(
            resolved=False,
            changed_state=False,
            reason="target_is_not_container",
            action=action,
            extra={"container_id": container_id},
        )

    if item_id == container_id:
        return _result(
            resolved=False,
            changed_state=False,
            reason="cannot_put_container_inside_itself",
            action=action,
            extra={"container_id": container_id},
        )

    remove_result = remove_quantity_from_items_list(
        items,
        item_id=item_id,
        quantity=quantity,
    )
    if not remove_result.get("removed_all"):
        return _result(
            resolved=False,
            changed_state=False,
            reason="item_not_available_for_container",
            action=action,
            extra={
                "item_id": item_id,
                "container_id": container_id,
                "quantity_missing": remove_result.get("quantity_missing"),
            },
        )

    removed_items = _safe_list(remove_result.get("removed_items"))
    moving_item = normalize_item_instance(_safe_dict(removed_items[0]))

    current_contents_weight = calculate_container_contents_weight(container_item)
    moving_weight = calculate_item_total_weight(moving_item)
    capacity = _safe_float(container.get("capacity_weight"), 0.0)

    if capacity > 0 and current_contents_weight + moving_weight > capacity:
        return _result(
            resolved=False,
            changed_state=False,
            reason="container_capacity_exceeded",
            action=action,
            extra={
                "item_id": item_id,
                "container_id": container_id,
                "contents_weight": current_contents_weight,
                "moving_weight": moving_weight,
                "capacity_weight": capacity,
            },
        )

    updated_items = _safe_list(remove_result.get("items"))
    container_index = _find_item_index(updated_items, container_id)
    if container_index < 0:
        return _result(
            resolved=False,
            changed_state=False,
            reason="container_missing_after_item_remove",
            action=action,
        )

    container_item = normalize_item_instance(updated_items[container_index])
    container = _safe_dict(container_item.get("container"))
    contents = _safe_list(container.get("items"))
    add_result = add_item_to_items_list(contents, moving_item)
    container["items"] = add_result["items"]
    container_item["container"] = container
    updated_items[container_index] = container_item

    inventory["items"] = updated_items
    inventory = recalculate_inventory_derived_fields(inventory)
    simulation_state["player_state"]["inventory"] = inventory

    return _result(
        resolved=True,
        changed_state=True,
        reason="item_added_to_container",
        action=action,
        extra={
            "item_id": item_id,
            "container_id": container_id,
            "quantity": quantity,
            "stacked": bool(add_result.get("stacked")),
            "contents_weight": calculate_container_contents_weight(container_item),
            "capacity_weight": capacity,
            "carry_weight": inventory.get("carry_weight"),
            "encumbrance_state": inventory.get("encumbrance_state"),
            "tick": int(tick or 0),
        },
    )