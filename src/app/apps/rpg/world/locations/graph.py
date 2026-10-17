from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any, Dict, List, cast
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str

SOURCE = "deterministic_phase4_location_graph"

RUSTY_FLAGON = "location:rusty_flagon"
MARKET = "location:market"
OLD_ROAD = "location:old_road"
OLD_MILL = "location:old_mill"
NEARBY_WILDERNESS = "location:nearby_wilderness"

CANONICAL_LOCATION_ORDER = [
    RUSTY_FLAGON,
    MARKET,
    OLD_ROAD,
    OLD_MILL,
    NEARBY_WILDERNESS,
]

CANONICAL_LOCATIONS: Dict[str, Dict[str, Any]] = {
    RUSTY_FLAGON: {
        "location_id": RUSTY_FLAGON,
        "name": "The Rusty Flagon",
        "description": "A smoky tavern and inn where Bran hears road rumors and rents rooms to travelers.",
        "services": ["service:inn_room", "service:tavern_meal", "service:rumors"],
        "npcs": ["npc:bran"],
        "hazards": [],
        "tags": ["settlement", "tavern", "safe"],
        "source": SOURCE,
    },
    MARKET: {
        "location_id": MARKET,
        "name": "Market Square",
        "description": "A small trade square with supplies, gossip, and routes back toward the tavern and road.",
        "services": ["service:merchant_buy", "service:merchant_sell"],
        "npcs": ["npc:elara"],
        "hazards": [],
        "tags": ["settlement", "commerce", "safe"],
        "source": SOURCE,
    },
    OLD_ROAD: {
        "location_id": OLD_ROAD,
        "name": "Old Road",
        "description": "The rutted road between town and the old mill, watched by wary travelers.",
        "services": [],
        "npcs": [],
        "hazards": ["hazard:bandit_ambush_risk", "hazard:rough_travel"],
        "tags": ["road", "travel", "danger"],
        "source": SOURCE,
    },
    OLD_MILL: {
        "location_id": OLD_MILL,
        "name": "Old Mill",
        "description": "A weathered mill near the road where bandit tracks and broken timbers mark danger.",
        "services": [],
        "npcs": ["npc:bandit_leader"],
        "hazards": ["hazard:bandit_camp", "hazard:unstable_floor"],
        "tags": ["wilderness", "quest", "danger"],
        "source": SOURCE,
    },
    NEARBY_WILDERNESS: {
        "location_id": NEARBY_WILDERNESS,
        "name": "Nearby Wilderness",
        "description": "Brush, shallow gullies, and game trails around the road and mill.",
        "services": [],
        "npcs": [],
        "hazards": ["hazard:wildlife", "hazard:exposure"],
        "tags": ["wilderness", "forage", "danger"],
        "source": SOURCE,
    },
}

CANONICAL_EDGES: List[Dict[str, Any]] = [
    {
        "edge_id": "route:rusty_flagon:old_road",
        "from": RUSTY_FLAGON,
        "to": OLD_ROAD,
        "name": "Town gate road",
        "description": "The road out from the Rusty Flagon toward the older trade route.",
        "bidirectional": True,
        "source": SOURCE,
    },
    {
        "edge_id": "route:rusty_flagon:market",
        "from": RUSTY_FLAGON,
        "to": MARKET,
        "name": "Tavern lane",
        "description": "The short lane between the Rusty Flagon and Market Square.",
        "bidirectional": True,
        "source": SOURCE,
    },
    {
        "edge_id": "route:market:old_road",
        "from": MARKET,
        "to": OLD_ROAD,
        "name": "Market cart track",
        "description": "A cart track merchants use to reach the old road.",
        "bidirectional": True,
        "source": SOURCE,
    },
    {
        "edge_id": "route:old_road:old_mill",
        "from": OLD_ROAD,
        "to": OLD_MILL,
        "name": "Mill spur",
        "description": "A narrow spur from the old road toward the abandoned mill.",
        "bidirectional": True,
        "source": SOURCE,
    },
    {
        "edge_id": "route:old_road:nearby_wilderness",
        "from": OLD_ROAD,
        "to": NEARBY_WILDERNESS,
        "name": "Brush trail",
        "description": "A faint trail from the road into nearby scrub and gullies.",
        "bidirectional": True,
        "source": SOURCE,
    },
]


def _location_exists(location_id: str) -> bool:
    return _safe_str(location_id) in CANONICAL_LOCATIONS


def _copy_dict(value: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(value)


def list_canonical_locations() -> List[Dict[str, Any]]:
    return [_copy_dict(CANONICAL_LOCATIONS[location_id]) for location_id in CANONICAL_LOCATION_ORDER]


def get_canonical_location(location_id: str) -> Dict[str, Any] | None:
    location = CANONICAL_LOCATIONS.get(_safe_str(location_id))
    return _copy_dict(location) if location else None


def list_canonical_edges() -> List[Dict[str, Any]]:
    return [_copy_dict(edge) for edge in CANONICAL_EDGES]


def list_location_exits(location_id: str) -> List[Dict[str, Any]]:
    location_id = _safe_str(location_id)
    exits: List[Dict[str, Any]] = []
    for edge in CANONICAL_EDGES:
        start = _safe_str(edge.get("from"))
        end = _safe_str(edge.get("to"))
        if start == location_id:
            row = _copy_dict(edge)
            row["destination_id"] = end
            exits.append(row)
        elif edge.get("bidirectional") is True and end == location_id:
            row = _copy_dict(edge)
            row["destination_id"] = start
            exits.append(row)
    return exits


def validate_location_graph() -> Dict[str, Any]:
    missing_locations: List[str] = []
    duplicate_edges: List[str] = []
    self_loops: List[str] = []
    seen_edge_ids: set[str] = set()
    for edge in CANONICAL_EDGES:
        edge_id = _safe_str(edge.get("edge_id"))
        if edge_id in seen_edge_ids:
            duplicate_edges.append(edge_id)
        seen_edge_ids.add(edge_id)
        start = _safe_str(edge.get("from"))
        end = _safe_str(edge.get("to"))
        if start == end:
            self_loops.append(edge_id)
        for location_id in (start, end):
            if location_id not in CANONICAL_LOCATIONS:
                missing_locations.append(location_id)
    location_source_missing = [
        location_id
        for location_id, location in CANONICAL_LOCATIONS.items()
        if location.get("source") != SOURCE or not location.get("name") or not location.get("description")
    ]
    edge_source_missing = [edge.get("edge_id") for edge in CANONICAL_EDGES if edge.get("source") != SOURCE]
    blockers = []
    if missing_locations:
        blockers.append({"kind": "missing_locations", "values": sorted(set(missing_locations)), "source": SOURCE})
    if duplicate_edges:
        blockers.append({"kind": "duplicate_edges", "values": sorted(set(duplicate_edges)), "source": SOURCE})
    if self_loops:
        blockers.append({"kind": "self_loops", "values": sorted(set(self_loops)), "source": SOURCE})
    if location_source_missing:
        blockers.append({"kind": "location_source_missing", "values": sorted(location_source_missing), "source": SOURCE})
    if edge_source_missing:
        blockers.append({"kind": "edge_source_missing", "values": sorted(cast(list[str], edge_source_missing)), "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "location_graph_valid" if not blockers else "location_graph_invalid",
        "location_count": len(CANONICAL_LOCATIONS),
        "edge_count": len(CANONICAL_EDGES),
        "blockers": blockers,
        "source": SOURCE,
    }


def find_location_route(start_location_id: str, end_location_id: str) -> Dict[str, Any]:
    start = _safe_str(start_location_id)
    end = _safe_str(end_location_id)
    unknown = [location_id for location_id in (start, end) if not _location_exists(location_id)]
    if unknown:
        return {"ok": False, "reason": "unknown_location", "unknown_locations": unknown, "source": SOURCE}
    queue: deque[tuple[str, list[str], list[Any]]] = deque([(start, [start], [])])
    visited = {start}
    while queue:
        current, path, edges = queue.popleft()
        if current == end:
            return {"ok": True, "reason": "route_found", "path": path, "edges": edges, "source": SOURCE}
        for edge in list_location_exits(current):
            destination = _safe_str(edge.get("destination_id"))
            if destination and destination not in visited:
                visited.add(destination)
                queue.append((destination, path + [destination], edges + [edge]))
    return {"ok": False, "reason": "route_unavailable", "path": [], "edges": [], "source": SOURCE}


def build_location_map_payload(current_location_id: str = RUSTY_FLAGON) -> Dict[str, Any]:
    current_location = get_canonical_location(current_location_id)
    return {
        "source": SOURCE,
        "current_location_id": _safe_str(current_location_id),
        "current_location": current_location,
        "locations": list_canonical_locations(),
        "edges": list_canonical_edges(),
        "visible_exits": list_location_exits(current_location_id) if current_location else [],
        "validation": validate_location_graph(),
    }


def build_location_narration_contract(map_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = map_payload if isinstance(map_payload, dict) else {}
    locations = raw_locations if isinstance(raw_locations := payload.get("locations"), list) else []
    edges = raw_edges if isinstance(raw_edges := payload.get("edges"), list) else []
    allowed = [f"Known location: {location.get('location_id')} — {location.get('name')}" for location in locations]
    allowed.extend(f"Known route: {edge.get('from')} -> {edge.get('to')}" for edge in edges)
    return {
        "source": SOURCE,
        "allowed_location_claims": allowed,
        "forbidden_location_claims": [
            "Do not invent locations that are not in the location graph.",
            "Do not invent routes, exits, services, NPCs, or hazards.",
            "Do not claim travel is available unless the deterministic graph exposes the route.",
        ],
    }


def assert_phase4_location_graph_ready() -> Dict[str, Any]:
    validation = validate_location_graph()
    old_mill_route = find_location_route(RUSTY_FLAGON, OLD_MILL)
    required_path = [RUSTY_FLAGON, OLD_ROAD, OLD_MILL]
    blockers = list(validation.get("blockers", []))
    if old_mill_route.get("path") != required_path:
        blockers.append({"kind": "missing_required_old_mill_path", "expected_path": required_path, "source": SOURCE})
    ok = validation.get("ok") is True and old_mill_route.get("path") == required_path and not blockers
    return {
        "ok": ok,
        "reason": "phase4_location_graph_ready" if ok else "phase4_location_graph_not_ready",
        "validation": validation,
        "required_old_mill_route": old_mill_route,
        "blockers": blockers,
        "source": SOURCE,
    }
