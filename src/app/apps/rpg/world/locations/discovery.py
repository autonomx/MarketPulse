from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.world.locations.graph import (
    MARKET,
    OLD_MILL,
    OLD_ROAD,
    RUSTY_FLAGON,
    find_location_route,
    get_canonical_location,
    list_location_exits,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase4_discovery_route_blocking"

STARTER_DISCOVERED_LOCATIONS = [RUSTY_FLAGON, MARKET, OLD_ROAD]
STARTER_DISCOVERED_ROUTES = ["route:rusty_flagon:old_road", "route:rusty_flagon:market", "route:market:old_road"]

DEFAULT_ROUTE_BLOCKS: Dict[str, Dict[str, Any]] = {
    "route:old_road:old_mill": {
        "edge_id": "route:old_road:old_mill",
        "blocked": True,
        "reason": "bandit_threat_unresolved",
        "summary": "The old mill spur is unsafe until the bandit threat is confronted or scouted.",
        "source": SOURCE,
    }
}


def _dedupe(values: List[Any]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        item = _safe_str(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def ensure_discovery_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    discovery_state = _safe_dict(state.get("discovery_state"))
    if not discovery_state:
        discovery_state = {}
        state["discovery_state"] = discovery_state
    discovered_locations = _dedupe(list(_safe_list(discovery_state.get("discovered_locations"))))
    discovered_routes = _dedupe(list(_safe_list(discovery_state.get("discovered_routes"))))
    route_blocks = _safe_dict(discovery_state.get("route_blocks"))
    if not discovered_locations:
        discovered_locations = list(STARTER_DISCOVERED_LOCATIONS)
    if not discovered_routes:
        discovered_routes = list(STARTER_DISCOVERED_ROUTES)
    if not route_blocks:
        route_blocks = deepcopy(DEFAULT_ROUTE_BLOCKS)
    discovery_state["discovered_locations"] = discovered_locations
    discovery_state["discovered_routes"] = discovered_routes
    discovery_state["route_blocks"] = route_blocks
    discovery_state["discovery_log"] = list(_safe_list(discovery_state.get("discovery_log")))
    discovery_state["source"] = SOURCE
    return discovery_state


def discover_location(
    simulation_state: Dict[str, Any],
    *,
    location_id: str,
    reason: str = "manual_discovery",
    turn_index: int = 0,
) -> Dict[str, Any]:
    location_id = _safe_str(location_id)
    if not get_canonical_location(location_id):
        return {"ok": False, "reason": "unknown_location", "location_id": location_id, "source": SOURCE}
    discovery_state = ensure_discovery_state(simulation_state)
    before = list(_safe_list(discovery_state.get("discovered_locations")))
    already_known = location_id in before
    if not already_known:
        discovery_state["discovered_locations"] = before + [location_id]
    entry = {
        "turn_index": int(turn_index or 0),
        "kind": "location_discovered" if not already_known else "location_already_discovered",
        "location_id": location_id,
        "reason": _safe_str(reason),
        "source": SOURCE,
    }
    discovery_state["discovery_log"] = list(_safe_list(discovery_state.get("discovery_log"))) + [entry]
    return {
        "ok": True,
        "reason": entry["kind"],
        "location_id": location_id,
        "already_known": already_known,
        "discovery_log_entry": entry,
        "source": SOURCE,
    }


def discover_route(
    simulation_state: Dict[str, Any],
    *,
    edge_id: str,
    reason: str = "manual_discovery",
    turn_index: int = 0,
) -> Dict[str, Any]:
    edge_id = _safe_str(edge_id)
    known_edge_ids = {_safe_str(edge.get("edge_id")) for edge in list_location_exits(RUSTY_FLAGON)}
    known_edge_ids.update(_safe_str(edge.get("edge_id")) for edge in list_location_exits(MARKET))
    known_edge_ids.update(_safe_str(edge.get("edge_id")) for edge in list_location_exits(OLD_ROAD))
    if edge_id not in known_edge_ids:
        return {"ok": False, "reason": "unknown_route", "edge_id": edge_id, "source": SOURCE}
    discovery_state = ensure_discovery_state(simulation_state)
    before = list(_safe_list(discovery_state.get("discovered_routes")))
    already_known = edge_id in before
    if not already_known:
        discovery_state["discovered_routes"] = before + [edge_id]
    entry = {
        "turn_index": int(turn_index or 0),
        "kind": "route_discovered" if not already_known else "route_already_discovered",
        "edge_id": edge_id,
        "reason": _safe_str(reason),
        "source": SOURCE,
    }
    discovery_state["discovery_log"] = list(_safe_list(discovery_state.get("discovery_log"))) + [entry]
    return {
        "ok": True,
        "reason": entry["kind"],
        "edge_id": edge_id,
        "already_known": already_known,
        "discovery_log_entry": entry,
        "source": SOURCE,
    }


def block_route(
    simulation_state: Dict[str, Any],
    *,
    edge_id: str,
    reason: str,
    summary: str,
    turn_index: int = 0,
) -> Dict[str, Any]:
    discovery_state = ensure_discovery_state(simulation_state)
    edge_id = _safe_str(edge_id)
    block = {
        "edge_id": edge_id,
        "blocked": True,
        "reason": _safe_str(reason),
        "summary": _safe_str(summary),
        "turn_index": int(turn_index or 0),
        "source": SOURCE,
    }
    route_blocks = dict(_safe_dict(discovery_state.get("route_blocks")))
    route_blocks[edge_id] = block
    discovery_state["route_blocks"] = route_blocks
    return {"ok": True, "reason": "route_blocked", "route_block": deepcopy(block), "source": SOURCE}


def unblock_route(
    simulation_state: Dict[str, Any], *, edge_id: str, reason: str, turn_index: int = 0) -> Dict[str, Any]:
    discovery_state = ensure_discovery_state(simulation_state)
    edge_id = _safe_str(edge_id)
    route_blocks = dict(_safe_dict(discovery_state.get("route_blocks")))
    existing = _safe_dict(route_blocks.get(edge_id))
    block = {
        "edge_id": edge_id,
        "blocked": False,
        "reason": _safe_str(reason),
        "summary": existing.get("summary", "Route is open."),
        "turn_index": int(turn_index or 0),
        "source": SOURCE,
    }
    route_blocks[edge_id] = block
    discovery_state["route_blocks"] = route_blocks
    return {"ok": True, "reason": "route_unblocked", "route_block": deepcopy(block), "source": SOURCE}


def get_route_block(discovery_state: Dict[str, Any], edge_id: str) -> Dict[str, Any] | None:
    block = _safe_dict(_safe_dict(discovery_state).get("route_blocks")).get(_safe_str(edge_id))
    return deepcopy(block) if isinstance(block, dict) else None


def is_route_blocked(discovery_state: Dict[str, Any], edge_id: str) -> bool:
    block = get_route_block(discovery_state, edge_id)
    return bool(block and block.get("blocked") is True)


def validate_route_access(simulation_state: Dict[str, Any], *, start_location_id: str, end_location_id: str) -> Dict[str, Any]:
    discovery_state = ensure_discovery_state(simulation_state)
    route = find_location_route(start_location_id, end_location_id)
    if not route.get("ok"):
        return {"ok": False, "reason": route.get("reason") or "route_unavailable", "route": route, "source": SOURCE}
    path = list(_safe_list(route.get("path")))
    discovered_locations = set(_safe_list(discovery_state.get("discovered_locations")))
    discovered_routes = set(_safe_list(discovery_state.get("discovered_routes")))
    unknown_locations = [location_id for location_id in path if location_id not in discovered_locations]
    unknown_routes = []
    blocked_routes = []
    for edge in _safe_list(route.get("edges")):
        edge_id = _safe_str(_safe_dict(edge).get("edge_id"))
        if edge_id not in discovered_routes:
            unknown_routes.append(edge_id)
        if is_route_blocked(discovery_state, edge_id):
            blocked_routes.append(get_route_block(discovery_state, edge_id))
    if unknown_locations:
        return {
            "ok": False,
            "reason": "undiscovered_location",
            "unknown_locations": unknown_locations,
            "route": route,
            "source": SOURCE,
        }
    if unknown_routes:
        return {
            "ok": False,
            "reason": "undiscovered_route",
            "unknown_routes": unknown_routes,
            "route": route,
            "source": SOURCE,
        }
    if blocked_routes:
        return {
            "ok": False,
            "reason": "route_blocked",
            "blocked_routes": blocked_routes,
            "route": route,
            "source": SOURCE,
        }
    return {"ok": True, "reason": "route_accessible", "route": route, "source": SOURCE}


def build_accessible_location_map_payload(simulation_state: Dict[str, Any], current_location_id: str = RUSTY_FLAGON) -> Dict[str, Any]:
    discovery_state = ensure_discovery_state(simulation_state)
    discovered_locations = set(_safe_list(discovery_state.get("discovered_locations")))
    discovered_routes = set(_safe_list(discovery_state.get("discovered_routes")))
    visible_exits = []
    for edge in list_location_exits(current_location_id):
        edge_id = _safe_str(edge.get("edge_id"))
        destination_id = _safe_str(edge.get("destination_id"))
        row = deepcopy(edge)
        row["discovered"] = edge_id in discovered_routes and destination_id in discovered_locations
        row["blocked"] = is_route_blocked(discovery_state, edge_id)
        row["block"] = get_route_block(discovery_state, edge_id)
        visible_exits.append(row)
    return {
        "source": SOURCE,
        "current_location_id": _safe_str(current_location_id),
        "discovered_locations": sorted(discovered_locations),
        "discovered_routes": sorted(discovered_routes),
        "visible_exits": visible_exits,
        "route_blocks": deepcopy(_safe_dict(discovery_state.get("route_blocks"))),
    }


def build_discovery_narration_contract(access_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(access_result)
    allowed_claims = [f"Route access result: {result.get('reason')}"]
    if result.get("reason") == "route_blocked":
        for block in _safe_list(result.get("blocked_routes")):
            row = _safe_dict(block)
            allowed_claims.append(f"Blocked route: {row.get('edge_id')} — {row.get('reason')}")
    if result.get("reason") == "undiscovered_location":
        allowed_claims.append(f"Undiscovered locations: {', '.join(_safe_list(result.get('unknown_locations')))}")
    if result.get("reason") == "undiscovered_route":
        allowed_claims.append(f"Undiscovered routes: {', '.join(_safe_list(result.get('unknown_routes')))}")
    return {
        "source": SOURCE,
        "allowed_discovery_claims": allowed_claims,
        "forbidden_discovery_claims": [
            "Do not reveal undiscovered locations as known to the player.",
            "Do not claim a blocked route is passable unless validate_route_access returned ok=true.",
            "Do not invent route block reasons or discovery state.",
        ],
    }


def assert_phase4_discovery_route_blocking_ready() -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    initial = validate_route_access(state, start_location_id=RUSTY_FLAGON, end_location_id=OLD_MILL)
    discover_location(state, location_id=OLD_MILL, reason="scouted_old_road", turn_index=2)
    discover_route(state, edge_id="route:old_road:old_mill", reason="scouted_old_road", turn_index=2)
    blocked = validate_route_access(state, start_location_id=RUSTY_FLAGON, end_location_id=OLD_MILL)
    unblock_route(state, edge_id="route:old_road:old_mill", reason="bandit_threat_resolved", turn_index=8)
    accessible = validate_route_access(state, start_location_id=RUSTY_FLAGON, end_location_id=OLD_MILL)
    blockers = []
    if initial.get("reason") != "undiscovered_location":
        blockers.append({"kind": "expected_initial_undiscovered_location", "actual": initial.get("reason"), "source": SOURCE})
    if blocked.get("reason") != "route_blocked":
        blockers.append({"kind": "expected_blocked_route", "actual": blocked.get("reason"), "source": SOURCE})
    if accessible.get("reason") != "route_accessible":
        blockers.append({"kind": "expected_accessible_route", "actual": accessible.get("reason"), "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase4_discovery_route_blocking_ready" if not blockers else "phase4_discovery_route_blocking_not_ready",
        "initial_access": initial,
        "blocked_access": blocked,
        "accessible_after_unblock": accessible,
        "blockers": blockers,
        "source": SOURCE,
    }
