from __future__ import annotations

from typing import Any, Dict, List, Tuple
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def inventory_items(simulation_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    inv = _safe_dict(
        simulation_state.get("player_inventory")
        or _safe_dict(simulation_state.get("player_state")).get("inventory")
        or _safe_dict(simulation_state.get("inventory_state")).get("player_inventory")
    )

    items = _safe_list(inv.get("items"))
    normalized = []
    for item in items:
        item = dict(_safe_dict(item))
        item_id = _safe_str(item.get("item_id") or item.get("id")).strip()
        if not item_id:
            continue
        item["item_id"] = item_id
        item["quantity"] = max(1, _safe_int(item.get("quantity"), 1))
        normalized.append(item)
    return normalized


def player_has_item(simulation_state: Dict[str, Any], item_id: str) -> bool:
    item_id = _safe_str(item_id).strip()
    if not item_id:
        return False
    return any(_safe_str(item.get("item_id")).strip() == item_id and _safe_int(item.get("quantity"), 0) > 0 for item in inventory_items(simulation_state))


def add_item_to_player_inventory(
    simulation_state: Dict[str, Any],
    item: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    simulation_state = dict(_safe_dict(simulation_state))
    item = dict(_safe_dict(item))
    item_id = _safe_str(item.get("item_id") or item.get("id")).strip()
    if not item_id:
        return simulation_state, {
            "added": False,
            "reason": "missing_item_id",
        }

    quantity = max(1, _safe_int(item.get("quantity"), 1))

    inv = dict(_safe_dict(simulation_state.get("player_inventory")))
    if not inv:
        inv = {"items": [], "equipment": {}, "carry_capacity": 50.0}

    items = [dict(_safe_dict(x)) for x in _safe_list(inv.get("items"))]
    merged = False
    for existing in items:
        if _safe_str(existing.get("item_id")).strip() == item_id:
            existing["quantity"] = max(1, _safe_int(existing.get("quantity"), 1)) + quantity
            merged = True
            break

    if not merged:
        new_item = dict(item)
        new_item["item_id"] = item_id
        new_item["quantity"] = quantity
        items.append(new_item)

    inv["items"] = items
    simulation_state["player_inventory"] = inv

    return simulation_state, {
        "added": True,
        "item_id": item_id,
        "quantity": quantity,
        "merged": merged,
    }