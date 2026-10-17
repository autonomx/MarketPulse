from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.rules.items.inventory_state import add_inventory_items, normalize_inventory_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


SERVICE_EFFECTS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "inn": {
        "common_room": {
            "restores": {
                "fatigue": -2,
            },
            "statuses_add": ["rested"],
            "lodging": "common_room",
        },
        "private_room": {
            "restores": {
                "fatigue": -4,
            },
            "statuses_add": ["rested", "well_rested"],
            "lodging": "private_room",
        },
        "meal": {
            "restores": {
                "hunger": -3,
            },
            "statuses_add": ["fed"],
        },
        "ale": {
            "statuses_add": ["tipsy"],
        },
    },
    "repair": {
        "basic_repair": {
            "repair_target": True,
            "repair_amount": 1,
        },
        "weapon_repair": {
            "repair_target": True,
            "repair_amount": 2,
        },
        "armor_repair": {
            "repair_target": True,
            "repair_amount": 2,
        },
    },
    "travel": {
        "local_passage": {
            "travel_flag": "local_passage_used",
        },
        "guarded_passage": {
            "travel_flag": "guarded_passage_used",
            "statuses_add": ["guard_escorted"],
        },
    },
}


def ensure_player_status_state(player_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = dict(_safe_dict(player_state))

    resources = _safe_dict(player_state.get("resources"))
    resources.setdefault("fatigue", 0)
    resources.setdefault("hunger", 0)
    resources.setdefault("thirst", 0)

    statuses = []
    seen = set()
    for value in _safe_list(player_state.get("statuses"))[:32]:
        text = _safe_str(value)
        if text and text not in seen:
            seen.add(text)
            statuses.append(text)

    service_flags = _safe_dict(player_state.get("service_flags"))
    lodging = _safe_str(player_state.get("lodging"))

    player_state["resources"] = resources
    player_state["statuses"] = statuses
    player_state["service_flags"] = service_flags
    player_state["lodging"] = lodging
    return player_state


def get_service_effect(service_type: str, service_id: str) -> Dict[str, Any]:
    service_type = _safe_str(service_type).lower()
    service_id = _safe_str(service_id).lower()
    return dict(_safe_dict(_safe_dict(SERVICE_EFFECTS.get(service_type)).get(service_id)))


def _add_statuses(player_state: Dict[str, Any], statuses_to_add: List[Any]) -> Dict[str, Any]:
    player_state = ensure_player_status_state(player_state)
    statuses = list(_safe_list(player_state.get("statuses")))
    seen = set(statuses)

    for value in _safe_list(statuses_to_add)[:16]:
        text = _safe_str(value)
        if text and text not in seen:
            seen.add(text)
            statuses.append(text)

    player_state["statuses"] = statuses[:32]
    return player_state


def _apply_resource_restores(player_state: Dict[str, Any], restores: Dict[str, Any]) -> Dict[str, Any]:
    player_state = ensure_player_status_state(player_state)
    resources = _safe_dict(player_state.get("resources"))

    for key, delta in _safe_dict(restores).items():
        resource_id = _safe_str(key)
        if not resource_id:
            continue
        current_value = _safe_int(resources.get(resource_id), 0)
        change = _safe_int(delta, 0)
        resources[resource_id] = max(0, current_value + change)

    player_state["resources"] = resources
    return player_state


def _apply_lodging(player_state: Dict[str, Any], lodging: str) -> Dict[str, Any]:
    player_state = ensure_player_status_state(player_state)
    player_state["lodging"] = _safe_str(lodging)
    return player_state


def _apply_travel_flag(player_state: Dict[str, Any], travel_flag: str) -> Dict[str, Any]:
    player_state = ensure_player_status_state(player_state)
    service_flags = _safe_dict(player_state.get("service_flags"))
    flag = _safe_str(travel_flag)
    if flag:
        service_flags[flag] = True
    player_state["service_flags"] = service_flags
    return player_state


def _apply_repair_effect(
    simulation_state: Dict[str, Any],
    action: Dict[str, Any],
    repair_amount: int,
) -> Dict[str, Any]:
    simulation_state = dict(_safe_dict(simulation_state))
    action = _safe_dict(action)

    player_state = _safe_dict(simulation_state.get("player_state"))
    inventory_state = normalize_inventory_state(_safe_dict(player_state.get("inventory_state")))
    items = list(_safe_list(inventory_state.get("items")))
    equipment = _safe_dict(inventory_state.get("equipment"))

    target_item_id = (
        _safe_str(action.get("repair_item_id"))
        or _safe_str(action.get("item_id"))
        or _safe_str(action.get("target_id"))
    )

    changed = False
    repair_amount = max(0, _safe_int(repair_amount, 0))

    def _repair_stack(stack: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal changed
        stack = dict(_safe_dict(stack))
        if target_item_id and _safe_str(stack.get("item_id")) != target_item_id:
            return stack

        durability = _safe_dict(stack.get("durability"))
        current_value = _safe_int(durability.get("current"), 0)
        max_value = max(current_value, _safe_int(durability.get("max"), current_value))
        if max_value <= 0:
            return stack

        updated_value = min(max_value, current_value + repair_amount)
        if updated_value != current_value:
            durability["current"] = updated_value
            durability["max"] = max_value
            stack["durability"] = durability
            changed = True
        return stack

    new_items = []
    for stack in items:
        new_items.append(_repair_stack(stack))

    new_equipment = {}
    for slot, stack in equipment.items():
        new_equipment[str(slot)] = _repair_stack(_safe_dict(stack))

    inventory_state["items"] = new_items
    inventory_state["equipment"] = new_equipment
    player_state["inventory_state"] = normalize_inventory_state(inventory_state)
    simulation_state["player_state"] = player_state

    return {
        "simulation_state": simulation_state,
        "repair_applied": changed,
        "repair_target_item_id": target_item_id,
        "repair_amount": repair_amount,
    }


def apply_item_purchase_effect(
    simulation_state: Dict[str, Any],
    action: Dict[str, Any],
) -> Dict[str, Any]:
    simulation_state = dict(_safe_dict(simulation_state))
    action = _safe_dict(action)

    player_state = _safe_dict(simulation_state.get("player_state"))
    inventory_state = normalize_inventory_state(_safe_dict(player_state.get("inventory_state")))

    item_id = (
        _safe_str(action.get("item_id"))
        or _safe_str(action.get("target_id"))
        or _safe_str(action.get("item"))
    )
    qty = max(1, _safe_int(action.get("quantity"), 1))
    item_name = _safe_str(action.get("item_name")) or item_id.replace("_", " ").title()

    if not item_id:
        return {
            "simulation_state": simulation_state,
            "effect_result": {
                "items_added": [],
                "service_effects": {},
            },
        }

    inventory_state = add_inventory_items(inventory_state, [{
        "item_id": item_id,
        "qty": qty,
        "name": item_name,
    }])

    player_state["inventory_state"] = normalize_inventory_state(inventory_state)
    simulation_state["player_state"] = player_state

    return {
        "simulation_state": simulation_state,
        "effect_result": {
            "items_added": [{
                "item_id": item_id,
                "qty": qty,
                "name": item_name,
            }],
            "service_effects": {},
        },
    }


def apply_service_purchase_effect(
    simulation_state: Dict[str, Any],
    action: Dict[str, Any],
) -> Dict[str, Any]:
    simulation_state = dict(_safe_dict(simulation_state))
    action = _safe_dict(action)

    player_state = ensure_player_status_state(_safe_dict(simulation_state.get("player_state")))

    action_type = _safe_str(action.get("action_type") or action.get("type")).lower()
    service_type = _safe_str(action.get("service_type")).lower()
    service_id = (
        _safe_str(action.get("service_id"))
        or _safe_str(action.get("target_id"))
        or _safe_str(action.get("service"))
    ).lower()

    if not service_type:
        if action_type in {"rent_room", "rent_bed"}:
            service_type = "inn"
        elif action_type == "pay":
            service_type = "service"

    if not service_id:
        if action_type == "rent_room":
            service_id = "private_room"
        elif action_type == "rent_bed":
            service_id = "common_room"

    effect = get_service_effect(service_type, service_id)
    if not effect:
        simulation_state["player_state"] = player_state
        return {
            "simulation_state": simulation_state,
            "effect_result": {
                "items_added": [],
                "service_effects": {},
            },
        }

    service_effects: Dict[str, Any] = {}

    restores = _safe_dict(effect.get("restores"))
    if restores:
        player_state = _apply_resource_restores(player_state, restores)
        service_effects["restores"] = restores

    statuses_add = _safe_list(effect.get("statuses_add"))
    if statuses_add:
        player_state = _add_statuses(player_state, statuses_add)
        service_effects["statuses_add"] = statuses_add

    lodging = _safe_str(effect.get("lodging"))
    if lodging:
        player_state = _apply_lodging(player_state, lodging)
        service_effects["lodging"] = lodging

    travel_flag = _safe_str(effect.get("travel_flag"))
    if travel_flag:
        player_state = _apply_travel_flag(player_state, travel_flag)
        service_effects["travel_flag"] = travel_flag

    simulation_state["player_state"] = player_state

    if bool(effect.get("repair_target")):
        repair_out = _apply_repair_effect(
            simulation_state,
            action,
            _safe_int(effect.get("repair_amount"), 0),
        )
        simulation_state = _safe_dict(repair_out.get("simulation_state"))
        service_effects["repair"] = {
            "applied": bool(repair_out.get("repair_applied")),
            "target_item_id": _safe_str(repair_out.get("repair_target_item_id")),
            "amount": _safe_int(repair_out.get("repair_amount"), 0),
        }

    return {
        "simulation_state": simulation_state,
        "effect_result": {
            "items_added": [],
            "service_effects": service_effects,
        },
    }


def apply_transaction_effects(
    simulation_state: Dict[str, Any],
    action: Dict[str, Any],
    action_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    simulation_state = dict(_safe_dict(simulation_state))
    action = _safe_dict(action)
    action_metadata = _safe_dict(action_metadata)

    kind = _safe_str(action_metadata.get("transaction_kind")).lower()

    if kind == "item_purchase":
        return apply_item_purchase_effect(simulation_state, action)

    if kind == "service_purchase":
        return apply_service_purchase_effect(simulation_state, action)

    return {
        "simulation_state": simulation_state,
        "effect_result": {
            "items_added": [],
            "service_effects": {},
        },
    }
