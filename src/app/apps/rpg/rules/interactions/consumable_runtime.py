from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.rules.interactions.item_model import (
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


def _find_item(items, item_id: str) -> Dict[str, Any]:
    for item in _safe_list(items):
        item = normalize_item_instance(_safe_dict(item))
        if _safe_str(item.get("item_id")) == item_id:
            return item
    return {}


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
        "source": "deterministic_consumable_runtime",
    }
    payload.update(_safe_dict(extra or {}))
    return payload


def apply_consumable_interaction(
    simulation_state: Dict[str, Any],
    *,
    semantic_action_v2: Dict[str, Any],
    tick: int = 0,
) -> Dict[str, Any]:
    action = _safe_dict(semantic_action_v2)
    if _safe_str(action.get("kind")) != "consume":
        return _result(
            resolved=False,
            changed_state=False,
            reason="not_consumable_action",
            action=action,
        )

    item_id = _safe_str(action.get("target_id"))
    if not item_id:
        return _result(
            resolved=False,
            changed_state=False,
            reason="missing_consumable_item",
            action=action,
        )

    inv = _inventory(simulation_state)
    items = [normalize_item_instance(_safe_dict(item)) for item in _safe_list(inv.get("items"))]
    item = _find_item(items, item_id)
    if not item:
        return _result(
            resolved=False,
            changed_state=False,
            reason="consumable_not_in_inventory",
            action=action,
            extra={"item_id": item_id},
        )

    consumable = _safe_dict(item.get("consumable"))
    if not consumable:
        return _result(
            resolved=False,
            changed_state=False,
            reason="item_is_not_consumable",
            action=action,
            extra={"item_id": item_id},
        )

    consumed_quantity = max(1, _safe_int(consumable.get("consumed_quantity"), 1))
    quantity_before = max(1, _safe_int(item.get("quantity"), 1))
    if quantity_before < consumed_quantity:
        return _result(
            resolved=False,
            changed_state=False,
            reason="insufficient_consumable_quantity",
            action=action,
            extra={
                "item_id": item_id,
                "required_quantity": consumed_quantity,
                "available_quantity": quantity_before,
            },
        )

    effect = _safe_dict(consumable.get("effect"))
    effect_kind = _safe_str(effect.get("kind"))

    player_state = _safe_dict(simulation_state.get("player_state"))
    effect_result = {
        "applied": False,
        "reason": "unsupported_consumable_effect",
        "effect": deepcopy(effect),
    }

    if effect_kind == "heal":
        amount = max(0, _safe_int(effect.get("amount"), 0))
        hp_before = _safe_int(player_state.get("hp"), _safe_int(player_state.get("max_hp"), 20))
        max_hp = max(1, _safe_int(player_state.get("max_hp"), 20))
        hp_after = min(max_hp, hp_before + amount)
        player_state["hp"] = hp_after
        player_state["max_hp"] = max_hp
        simulation_state["player_state"] = player_state
        effect_result = {
            "applied": True,
            "kind": "heal",
            "amount": amount,
            "hp_before": hp_before,
            "hp_after": hp_after,
            "max_hp": max_hp,
        }

    remove_result = remove_quantity_from_items_list(
        items,
        item_id=item_id,
        quantity=consumed_quantity,
    )
    if not remove_result.get("removed_all"):
        return _result(
            resolved=False,
            changed_state=False,
            reason="failed_to_consume_item_quantity",
            action=action,
            extra={"item_id": item_id},
        )

    inv["items"] = _safe_list(remove_result.get("items"))
    inv = recalculate_inventory_derived_fields(inv)
    simulation_state["player_state"]["inventory"] = inv

    return _result(
        resolved=True,
        changed_state=True,
        reason="consumable_used",
        action=action,
        extra={
            "item_id": item_id,
            "definition_id": _safe_str(item.get("definition_id")),
            "effect": deepcopy(effect),
            "effect_result": effect_result,
            "quantity_before": quantity_before,
            "quantity_after": quantity_before - consumed_quantity,
            "quantity_consumed": consumed_quantity,
            "carry_weight": inv.get("carry_weight"),
            "encumbrance_state": inv.get("encumbrance_state"),
            "tick": int(tick or 0),
        },
    )
