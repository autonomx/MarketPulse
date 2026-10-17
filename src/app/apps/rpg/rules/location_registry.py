from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


LOCATIONS: Dict[str, Dict[str, Any]] = {
    "loc_tavern": {
        "location_id": "loc_tavern",
        "name": "The Rusty Flagon Tavern",
        "short_name": "tavern",
        "aliases": ["tavern", "inn", "rusty flagon", "the rusty flagon"],
        "description": "A warm tavern with worn beams, busy tables, and the smell of stew.",
        "present_npcs": [
            {"id": "npc:Bran", "name": "Bran", "role": "innkeeper"},
            {"id": "npc:Mira", "name": "Mira", "role": "server"},
        ],
        "services": ["lodging", "meal", "paid_information"],
        "exits": {
            "market": "loc_market",
            "market square": "loc_market",
            "outside": "loc_market",
        },
    },
    "loc_market": {
        "location_id": "loc_market",
        "name": "Market Square",
        "short_name": "market",
        "aliases": ["market", "market square", "square", "bazaar"],
        "description": "A busy market square of stalls, carts, shouted prices, and foot traffic.",
        "present_npcs": [
            {"id": "npc:Elara", "name": "Elara", "role": "merchant"},
            {"id": "npc:Tomas", "name": "Tomas", "role": "porter"},
        ],
        "services": ["shop_goods", "repair"],
        "exits": {
            "tavern": "loc_tavern",
            "inn": "loc_tavern",
            "rusty flagon": "loc_tavern",
            "old mill road": "loc_old_mill_road",
        },
    },
    "loc_old_mill_road": {
        "location_id": "loc_old_mill_road",
        "name": "Old Mill Road",
        "short_name": "old mill road",
        "aliases": ["old mill", "old mill road", "mill road"],
        "description": "A rutted road leading toward the old mill and the quieter edge of town.",
        "present_npcs": [],
        "services": [],
        "exits": {
            "market": "loc_market",
            "market square": "loc_market",
        },
    },
}


def get_location(location_id: str) -> Dict[str, Any]:
    return deepcopy(_safe_dict(LOCATIONS.get(_safe_str(location_id))))


def default_location_id() -> str:
    return "loc_tavern"


def current_location_id(simulation_state: Dict[str, Any]) -> str:
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    location_id = (
        _safe_str(player_state.get("location_id"))
        or _safe_str(player_state.get("current_location_id"))
        or _safe_str(simulation_state.get("location_id"))
        or _safe_str(simulation_state.get("current_location_id"))
    )
    if not location_id:
        return default_location_id()
    if location_id in LOCATIONS:
        return location_id
    matched = find_location_by_name(location_id)
    return _safe_str(matched.get("location_id")) or location_id


def has_explicit_location(simulation_state: Dict[str, Any]) -> bool:
    """Return True only if an explicit location is stored in the simulation state."""
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    return bool(
        _safe_str(player_state.get("location_id"))
        or _safe_str(player_state.get("current_location_id"))
        or _safe_str(simulation_state.get("location_id"))
        or _safe_str(simulation_state.get("current_location_id"))
        or _safe_dict(simulation_state.get("location_state")).get("current_location_id")
    )


def normalize_location_name(value: str) -> str:
    value = _safe_str(value).strip().lower()
    if value.startswith("location:"):
        value = value.split(":", 1)[1]
    value = value.replace("_", "-").replace("-", " ")
    value = value.replace("the ", "", 1) if value.startswith("the ") else value
    return " ".join(value.split())


def find_location_by_name(value: str) -> Dict[str, Any]:
    needle = normalize_location_name(value)
    if not needle:
        return {}

    for location in LOCATIONS.values():
        names = [
            _safe_str(location.get("location_id")),
            _safe_str(location.get("name")),
            _safe_str(location.get("short_name")),
            *_safe_list(location.get("aliases")),
        ]
        if any(normalize_location_name(name) == needle for name in names):
            return deepcopy(location)

    for location in LOCATIONS.values():
        names = [
            _safe_str(location.get("name")),
            _safe_str(location.get("short_name")),
            *_safe_list(location.get("aliases")),
        ]
        if any(normalize_location_name(name) in needle for name in names):
            return deepcopy(location)

    return {}


def available_exits(simulation_state: Dict[str, Any]) -> Dict[str, str]:
    location = get_location(current_location_id(simulation_state))
    return dict(_safe_dict(location.get("exits")))


def set_current_location(simulation_state: Dict[str, Any], location_id: str) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    location_id = _safe_str(location_id) or default_location_id()
    location = get_location(location_id)
    if not location:
        location_id = default_location_id()
        location = get_location(location_id)

    player_state = _safe_dict(simulation_state.get("player_state"))
    if not player_state:
        player_state = {}
        simulation_state["player_state"] = player_state

    simulation_state["location_id"] = location_id
    simulation_state["current_location_id"] = location_id
    simulation_state["location_state"] = {
        "current_location_id": location_id,
        "current_location": location,
        "available_exits": available_exits({"location_id": location_id}),
    }
    simulation_state["present_npcs"] = deepcopy(_safe_list(location.get("present_npcs")))
    player_state["location_id"] = location_id
    player_state["current_location_id"] = location_id
    return simulation_state


def ensure_location_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    location_id = current_location_id(simulation_state)
    return set_current_location(simulation_state, location_id)


def present_npcs_for_current_location(simulation_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    location = get_location(current_location_id(simulation_state))
    return deepcopy(_safe_list(location.get("present_npcs")))


def provider_present_at_location(simulation_state: Dict[str, Any], provider_id: str = "", provider_name: str = "") -> bool:
    location = get_location(current_location_id(simulation_state))
    npcs = _safe_list(location.get("present_npcs"))
    provider_id = _safe_str(provider_id)
    provider_name = _safe_str(provider_name).lower()
    for npc in npcs:
        npc = _safe_dict(npc)
        if provider_id and _safe_str(npc.get("id")) == provider_id:
            return True
        if provider_name and _safe_str(npc.get("name")).lower() == provider_name:
            return True
    return False


def location_allows_service(simulation_state: Dict[str, Any], service_kind: str) -> bool:
    location = get_location(current_location_id(simulation_state))
    return _safe_str(service_kind) in set(_safe_list(location.get("services")))
