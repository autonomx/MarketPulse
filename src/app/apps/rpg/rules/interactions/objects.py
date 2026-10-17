from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


DEFAULT_OBJECTS: Dict[str, Dict[str, Any]] = {
    "object:old_chest": {
        "object_id": "object:old_chest",
        "name": "Old Chest",
        "kind": "container",
        "location_id": "loc_tavern_cellar",
        "area_id": "cellar",
        "locked": True,
        "open": False,
        "required_key_id": "item:iron_key",
        "reachable": True,
        "visible": True,
        "contents": [
            {
                "item_id": "item:copper_coin",
                "name": "Copper coin",
                "quantity": 12,
            }
        ],
    },
    "object:unlocked_crate": {
        "object_id": "object:unlocked_crate",
        "name": "Unlocked Crate",
        "kind": "container",
        "location_id": "loc_tavern_cellar",
        "area_id": "cellar",
        "locked": False,
        "open": False,
        "required_key_id": "",
        "reachable": True,
        "visible": True,
        "contents": [
            {
                "item_id": "item:apple",
                "name": "Apple",
                "quantity": 1,
            }
        ],
    },
    "object:cellar_door": {
        "object_id": "object:cellar_door",
        "name": "Cellar Door",
        "kind": "door",
        "location_id": "loc_rusty_flagon",
        "area_id": "common_room",
        "connects": ["common_room", "cellar"],
        "locked": True,
        "open": False,
        "required_key_id": "item:cellar_key",
        "reachable": True,
        "visible": True,
        "blocks_movement": True,
    },
}


def normalize_world_object(value: Any) -> Dict[str, Any]:
    obj = dict(_safe_dict(value))
    if not obj:
        return {}

    object_id = _safe_str(obj.get("object_id") or obj.get("id")).strip()
    if object_id:
        obj["object_id"] = object_id
        obj["id"] = object_id

    obj["name"] = _safe_str(obj.get("name") or object_id).strip()
    obj["kind"] = _safe_str(obj.get("kind") or "object").strip().lower()
    obj["location_id"] = _safe_str(obj.get("location_id")).strip()
    obj["area_id"] = _safe_str(obj.get("area_id")).strip()
    obj["locked"] = _safe_bool(obj.get("locked"), False)
    obj["open"] = _safe_bool(obj.get("open"), False)
    obj["required_key_id"] = _safe_str(obj.get("required_key_id")).strip()
    obj["reachable"] = _safe_bool(obj.get("reachable"), True)
    obj["visible"] = _safe_bool(obj.get("visible"), True)
    obj["contents"] = [
        dict(_safe_dict(item))
        for item in _safe_list(obj.get("contents"))
        if _safe_dict(item).get("item_id")
    ]
    return obj


def normalize_world_objects(value: Any) -> Dict[str, Dict[str, Any]]:
    raw = _safe_dict(value)
    normalized: Dict[str, Dict[str, Any]] = {}
    for object_id, obj in raw.items():
        obj = normalize_world_object(obj)
        oid = _safe_str(obj.get("object_id") or object_id).strip()
        if oid:
            normalized[oid] = obj
    return normalized


def world_objects_from_state(simulation_state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    objects = normalize_world_objects(
        simulation_state.get("world_objects")
        or _safe_dict(simulation_state.get("interaction_state")).get("world_objects")
    )

    # Keep defaults as fallback only. Scenario/state objects override defaults.
    merged = {k: deepcopy(v) for k, v in DEFAULT_OBJECTS.items()}
    merged.update(objects)
    return normalize_world_objects(merged)


def set_world_object(
    simulation_state: Dict[str, Any],
    object_id: str,
    obj: Dict[str, Any],
) -> Dict[str, Any]:
    simulation_state = dict(_safe_dict(simulation_state))
    objects = world_objects_from_state(simulation_state)
    objects[_safe_str(object_id).strip()] = normalize_world_object(obj)
    simulation_state["world_objects"] = objects
    return simulation_state


def find_object_id_from_text(
    simulation_state: Dict[str, Any],
    player_input: str,
) -> str:
    text = _safe_str(player_input).strip().lower()
    objects = world_objects_from_state(simulation_state)

    aliases = {
        "chest": "object:old_chest",
        "old chest": "object:old_chest",
        "crate": "object:unlocked_crate",
        "unlocked crate": "object:unlocked_crate",
        "cellar door": "object:cellar_door",
        "door": "object:cellar_door",
    }

    for phrase, object_id in aliases.items():
        if phrase in text and object_id in objects:
            return object_id

    for object_id, obj in objects.items():
        name = _safe_str(obj.get("name")).strip().lower()
        if name and name in text:
            return object_id
        normalized_id = object_id.replace("object:", "").replace("_", " ").replace(":", " ").lower()
        if normalized_id and normalized_id in text:
            return object_id

    return ""


def item_id_from_text(player_input: str) -> str:
    text = _safe_str(player_input).strip().lower()
    aliases = {
        "iron key": "item:iron_key",
        "cellar key": "item:cellar_key",
        "key": "item:iron_key",
    }
    for phrase, item_id in aliases.items():
        if phrase in text:
            return item_id
    return ""