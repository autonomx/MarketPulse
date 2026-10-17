from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.rules.items.inventory_state import (
    add_inventory_items,
    equip_inventory_item,
    get_inventory_item_for_drop,
    remove_inventory_item,
    unequip_inventory_slot,
)
from app.apps.rpg.rules.items.world_items import drop_world_item, pickup_world_item
from app.apps.rpg.session.state_normalization import _safe_str
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def extract_equipment(player_state: Dict[str, Any]) -> Dict[str, Any]:
    inventory_state = _safe_dict(player_state).get("inventory_state")
    inventory_state = _safe_dict(inventory_state)
    return _safe_dict(inventory_state.get("equipment"))



def pickup_item_action(
    simulation_state: Dict[str, Any],
    action: Dict[str, Any],
) -> Dict[str, Any]:
    instance_id = _safe_str(action.get("instance_id")).strip()
    result = pickup_world_item(simulation_state, instance_id)
    next_state = _safe_dict(result.get("simulation_state"))
    picked_item = _safe_dict(result.get("picked_up_item"))
    if picked_item.get("item_id"):
        player_state = _safe_dict(next_state.get("player_state"))
        inventory_state = _safe_dict(player_state.get("inventory_state"))
        inventory_state = add_inventory_items(inventory_state, [picked_item])
        player_state["inventory_state"] = inventory_state
        next_state["player_state"] = player_state
    return {
        "simulation_state": next_state,
        "result": _safe_dict(result.get("result")),
        "picked_up_item": picked_item,
    }



def drop_item_action(
    simulation_state: Dict[str, Any],
    action: Dict[str, Any],
    *,
    location_id: str,
) -> Dict[str, Any]:
    item_id = _safe_str(action.get("item_id")).strip()
    qty = int(action.get("qty", 1) or 1)
    player_state = _safe_dict(simulation_state.get("player_state"))
    inventory_state = _safe_dict(player_state.get("inventory_state"))
    dropped_item = get_inventory_item_for_drop(inventory_state, item_id)
    inventory_state = remove_inventory_item(inventory_state, item_id, qty=qty)
    player_state["inventory_state"] = inventory_state
    simulation_state["player_state"] = player_state

    drop_payload = dropped_item if dropped_item else {"item_id": item_id, "qty": qty}
    result = drop_world_item(simulation_state, drop_payload, location_id, qty=qty)
    next_state = _safe_dict(result.get("simulation_state"))
    return {
        "simulation_state": next_state,
        "result": _safe_dict(result.get("result")),
    }



def equip_item_action(
    simulation_state: Dict[str, Any],
    action: Dict[str, Any],
) -> Dict[str, Any]:
    item_id = _safe_str(action.get("item_id")).strip()
    slot = _safe_str(action.get("slot")).strip()
    player_state = _safe_dict(simulation_state.get("player_state"))
    inventory_state = _safe_dict(player_state.get("inventory_state"))
    inventory_state = equip_inventory_item(inventory_state, item_id, slot)
    player_state["inventory_state"] = inventory_state
    simulation_state["player_state"] = player_state
    return {
        "simulation_state": simulation_state,
        "result": {
            "ok": True,
            "action_type": "equip_item",
            "item_id": item_id,
            "slot": slot or _safe_str(_safe_dict(extract_equipment(player_state)).get("main_hand")),
            "equipment": _safe_dict(inventory_state.get("equipment")),
        },
    }



def unequip_item_action(
    simulation_state: Dict[str, Any],
    action: Dict[str, Any],
) -> Dict[str, Any]:
    slot = _safe_str(action.get("slot")).strip()
    player_state = _safe_dict(simulation_state.get("player_state"))
    inventory_state = _safe_dict(player_state.get("inventory_state"))
    inventory_state = unequip_inventory_slot(inventory_state, slot)
    player_state["inventory_state"] = inventory_state
    simulation_state["player_state"] = player_state
    return {
        "simulation_state": simulation_state,
        "result": {
            "ok": True,
            "action_type": "unequip_item",
            "slot": slot,
            "equipment": _safe_dict(inventory_state.get("equipment")),
        },
    }
