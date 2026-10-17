"""Phase 18.3A — World item state: ground loot and scene objects."""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _copy_item_record(item: Dict[str, Any]) -> Dict[str, Any]:
    item = _safe_dict(item)
    return {
        "item_id": _safe_str(item.get("item_id")),
        "qty": _safe_int(item.get("qty"), 1) or 1,
        "name": _safe_str(item.get("name")),
        "category": _safe_str(item.get("category")),
        "tags": _safe_list(item.get("tags")),
        "description": _safe_str(item.get("description")),
        "combat_stats": _safe_dict(item.get("combat_stats")),
        "equipment": _safe_dict(item.get("equipment")),
        "quality": _safe_dict(item.get("quality")),
        "value": _safe_int(item.get("value"), 0),
        "instance_id": _safe_str(item.get("instance_id")),
        "durability": _safe_int(item.get("durability"), 100),
    }


def ensure_world_item_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure simulation_state has a well-formed world_items subtree."""
    simulation_state = dict(simulation_state or {})
    wi = simulation_state.setdefault("world_items", {})
    if not isinstance(wi, dict):
        simulation_state["world_items"] = {"by_location": {}}
    wi = simulation_state["world_items"]
    wi.setdefault("by_location", {})
    return simulation_state


def spawn_world_item(
    simulation_state: Dict[str, Any],
    location_id: str,
    item_def: Dict[str, Any],
) -> Dict[str, Any]:
    """Spawn an item in a world location."""
    simulation_state = ensure_world_item_state(simulation_state)
    location_id = _safe_str(location_id) or "unknown"
    item_def = dict(item_def or {})
    item_id = _safe_str(item_def.get("item_id"))
    # Generate deterministic instance_id
    content = f"{location_id}-{item_id}-{len(simulation_state['world_items']['by_location'].get(location_id, []))}"
    instance_id = item_def.get("instance_id") or ("wi_" + hashlib.sha256(content.encode()).hexdigest()[:10])
    item_def["instance_id"] = str(instance_id)
    item_def["location_id"] = location_id

    by_loc = simulation_state["world_items"]["by_location"]
    if location_id not in by_loc:
        by_loc[location_id] = []
    by_loc[location_id].append(item_def)
    return simulation_state


def pickup_world_item(
    simulation_state: Dict[str, Any],
    item_instance_id: str,
) -> Dict[str, Any]:
    simulation_state = ensure_world_item_state(simulation_state)
    by_location = _safe_dict(_safe_dict(simulation_state.get("world_items")).get("by_location"))

    picked_item: Dict[str, Any] = {}
    found_location_id = ""

    for location_id, items in by_location.items():
        normalized_items = []
        for item in _safe_list(items):
            item = _safe_dict(item)
            if _safe_str(item.get("instance_id")) == item_instance_id:
                picked_item = item
                found_location_id = location_id
                continue
            normalized_items.append(item)
        by_location[location_id] = normalized_items

    simulation_state["world_items"] = {"by_location": by_location}

    if not picked_item:
        return {
            "simulation_state": simulation_state,
            "picked_up_item": {},
            "result": {
                "ok": False,
                "reason": "item_not_found",
                "instance_id": item_instance_id,
            },
        }

    return {
        "simulation_state": simulation_state,
        "picked_up_item": picked_item,
        "result": {
            "ok": True,
            "action_type": "pickup_item",
            "instance_id": item_instance_id,
            "item_id": _safe_str(picked_item.get("item_id")),
            "location_id": found_location_id,
        },
    }


def drop_world_item(
    simulation_state: Dict[str, Any],
    item_or_item_id: Any,
    location_id: str,
    qty: int = 1,
) -> Dict[str, Any]:
    simulation_state = ensure_world_item_state(simulation_state)
    world_items = _safe_dict(simulation_state.get("world_items"))
    by_location = _safe_dict(world_items.get("by_location"))
    items = _safe_list(by_location.get(location_id))

    if isinstance(item_or_item_id, dict):
        dropped_item = _copy_item_record(item_or_item_id)
        dropped_item["qty"] = int(qty or dropped_item.get("qty", 1) or 1)
        dropped_item["instance_id"] = _safe_str(dropped_item.get("instance_id")) or f"wi:{location_id}:{_safe_str(dropped_item.get('item_id'))}:{len(items)}"
        item_id = _safe_str(dropped_item.get("item_id"))
    else:
        item_id = _safe_str(item_or_item_id)
        dropped_item = {
            "item_id": item_id,
            "qty": int(qty or 1),
            "instance_id": f"wi:{location_id}:{item_id}:{len(items)}",
            "_degraded": True,
        }
    items.append(dropped_item)
    by_location[location_id] = items
    simulation_state["world_items"] = {"by_location": by_location}

    return {
        "simulation_state": simulation_state,
        "result": {
            "ok": True,
            "action_type": "drop_item",
            "item_id": item_id,
            "location_id": location_id,
            "qty": int(qty or 1),
            "dropped_item": dropped_item,
        },
    }


def list_scene_items(
    simulation_state: Dict[str, Any],
    location_id: str,
) -> List[Dict[str, Any]]:
    """Return items present at a location."""
    simulation_state = ensure_world_item_state(simulation_state)
    location_id = _safe_str(location_id)
    by_loc = _safe_dict(simulation_state.get("world_items", {}).get("by_location"))
    items = by_loc.get(location_id, [])
    return [dict(i) for i in items if isinstance(i, dict)]
