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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
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


def _find_index(items, item_id: str) -> int:
    for idx, item in enumerate(_safe_list(items)):
        if _safe_str(_safe_dict(item).get("item_id")) == item_id:
            return idx
    return -1


def _tags(item: Dict[str, Any]) -> set[str]:
    return {_safe_str(tag) for tag in _safe_list(item.get("tags")) if _safe_str(tag)}


def _condition(item: Dict[str, Any]) -> Dict[str, float]:
    cond = _safe_dict(item.get("condition"))
    return {
        "durability": max(0.0, min(1.0, _safe_float(cond.get("durability"), 1.0))),
        "max_durability": max(0.0, _safe_float(cond.get("max_durability"), 1.0)),
    }


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
        "source": "deterministic_repair_runtime",
    }
    payload.update(_safe_dict(extra or {}))
    return payload


def _repair_config(item: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_dict(item.get("repair"))


def apply_repair_interaction(
    simulation_state: Dict[str, Any],
    *,
    semantic_action_v2: Dict[str, Any],
    tick: int = 0,
) -> Dict[str, Any]:
    action = _safe_dict(semantic_action_v2)
    if _safe_str(action.get("kind")) != "repair":
        return _result(
            resolved=False,
            changed_state=False,
            reason="not_repair_action",
            action=action,
        )

    target_id = _safe_str(action.get("target_id"))
    tool_or_material_id = _safe_str(action.get("secondary_target_id"))

    inv = _inventory(simulation_state)
    items = [normalize_item_instance(_safe_dict(item)) for item in _safe_list(inv.get("items"))]

    target_index = _find_index(items, target_id)
    if target_index < 0:
        return _result(
            resolved=False,
            changed_state=False,
            reason="repair_target_not_in_inventory",
            action=action,
            extra={"target_item_id": target_id},
        )

    target = normalize_item_instance(items[target_index])
    cond = _condition(target)
    before = cond["durability"]

    if before >= cond["max_durability"]:
        return _result(
            resolved=False,
            changed_state=False,
            reason="item_already_fully_repaired",
            action=action,
            extra={"target_item_id": target_id, "condition_before": before},
        )

    if not tool_or_material_id:
        return _result(
            resolved=False,
            changed_state=False,
            reason="missing_repair_tool_or_material",
            action=action,
            extra={"target_item_id": target_id},
        )

    tool_index = _find_index(items, tool_or_material_id)
    if tool_index < 0:
        return _result(
            resolved=False,
            changed_state=False,
            reason="repair_tool_or_material_not_in_inventory",
            action=action,
            extra={"target_item_id": target_id, "tool_or_material_id": tool_or_material_id},
        )

    repair_item = normalize_item_instance(items[tool_index])
    repair_cfg = _repair_config(repair_item)
    target_tags = _tags(target)
    allowed_tags = {_safe_str(tag) for tag in _safe_list(repair_cfg.get("target_tags")) if _safe_str(tag)}

    if allowed_tags and not (allowed_tags & target_tags):
        return _result(
            resolved=False,
            changed_state=False,
            reason="repair_material_not_applicable",
            action=action,
            extra={
                "target_item_id": target_id,
                "tool_or_material_id": tool_or_material_id,
                "target_tags": sorted(target_tags),
                "allowed_tags": sorted(allowed_tags),
            },
        )

    materials_consumed = []

    if repair_cfg.get("tool") is True:
        restore = _safe_float(repair_cfg.get("durability_restore"), 0.0)
        reason = "item_repaired_with_tool"
    elif repair_cfg.get("material") is True:
        requested_qty = max(1, _safe_int(action.get("secondary_quantity"), repair_cfg.get("default_quantity") or 1))
        available_qty = _safe_int(repair_item.get("quantity"), 1)
        if available_qty < requested_qty:
            return _result(
                resolved=False,
                changed_state=False,
                reason="insufficient_repair_material_quantity",
                action=action,
                extra={
                    "target_item_id": target_id,
                    "material_item_id": tool_or_material_id,
                    "required_quantity": requested_qty,
                    "available_quantity": available_qty,
                },
            )

        restore = _safe_float(repair_cfg.get("durability_restore_per_unit"), 0.0) * requested_qty
        reason = "item_repaired_with_material"

        remove_result = remove_quantity_from_items_list(
            items,
            item_id=tool_or_material_id,
            quantity=requested_qty,
        )
        if not remove_result.get("removed_all"):
            return _result(
                resolved=False,
                changed_state=False,
                reason="failed_to_consume_repair_material",
                action=action,
            )
        items = _safe_list(remove_result.get("items"))
        target_index = _find_index(items, target_id)
        if target_index < 0:
            return _result(
                resolved=False,
                changed_state=False,
                reason="repair_target_missing_after_material_consumption",
                action=action,
                extra={
                    "target_item_id": target_id,
                    "material_item_id": tool_or_material_id,
                },
            )
        materials_consumed.append({
            "item_id": tool_or_material_id,
            "definition_id": _safe_str(repair_item.get("definition_id")),
            "quantity": requested_qty,
        })
    else:
        return _result(
            resolved=False,
            changed_state=False,
            reason="item_is_not_repair_tool_or_material",
            action=action,
            extra={"tool_or_material_id": tool_or_material_id},
        )

    target = normalize_item_instance(items[target_index])
    cond = _condition(target)
    before = cond["durability"]
    after = min(cond["max_durability"], before + restore)
    target["condition"] = {
        "durability": round(after, 4),
        "max_durability": cond["max_durability"],
    }
    items[target_index] = target

    inv["items"] = items
    inv = recalculate_inventory_derived_fields(inv)
    simulation_state["player_state"]["inventory"] = inv

    return _result(
        resolved=True,
        changed_state=True,
        reason=reason,
        action=action,
        extra={
            "target_item_id": target_id,
            "tool_or_material_id": tool_or_material_id,
            "condition_before": round(before, 4),
            "condition_after": round(after, 4),
            "durability_restored": round(after - before, 4),
            "materials_consumed": materials_consumed,
            "carry_weight": inv.get("carry_weight"),
            "encumbrance_state": inv.get("encumbrance_state"),
            "tick": int(tick or 0),
        },
    )