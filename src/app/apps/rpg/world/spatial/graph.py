from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.world.spatial.serialization import normalize_spatial_graph
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def get_spatial_graph(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    return normalize_spatial_graph(_safe_dict(simulation_state).get("spatial_graph"))


def ensure_spatial_graph(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    graph = normalize_spatial_graph(simulation_state.get("spatial_graph"))
    simulation_state["spatial_graph"] = graph
    return graph


def list_area_connections(graph: Dict[str, Any], area_id: str) -> List[Dict[str, Any]]:
    graph = normalize_spatial_graph(graph)
    results: List[Dict[str, Any]] = []
    for connection in graph["connections"].values():
        if connection.get("from_area_id") == area_id:
            results.append(dict(connection))
        elif connection.get("bidirectional") and connection.get("to_area_id") == area_id:
            reverse = dict(connection)
            reverse["from_area_id"] = connection.get("to_area_id")
            reverse["to_area_id"] = connection.get("from_area_id")
            reverse["reversed"] = True
            results.append(reverse)
    return results


def find_connection(
    graph: Dict[str, Any],
    from_area_id: str,
    to_area_id: str,
) -> Dict[str, Any] | None:
    for connection in list_area_connections(graph, from_area_id):
        if connection.get("to_area_id") == to_area_id:
            return connection
    return None


def get_entity_area(graph: Dict[str, Any], entity_id: str) -> str:
    graph = normalize_spatial_graph(graph)
    location = graph["entity_locations"].get(entity_id) or {}
    return str(location.get("area_id") or "")


def set_entity_area(
    graph: Dict[str, Any],
    entity_id: str,
    area_id: str,
    *,
    hidden: bool | None = None,
    silent: bool | None = None,
) -> Dict[str, Any]:
    graph = normalize_spatial_graph(graph)
    location = dict(graph["entity_locations"].get(entity_id) or {})
    location["entity_id"] = entity_id
    location["area_id"] = area_id
    if hidden is not None:
        location["hidden"] = bool(hidden)
    else:
        location.setdefault("hidden", False)
    if silent is not None:
        location["silent"] = bool(silent)
    else:
        location.setdefault("silent", False)
    location.setdefault("tags", [])
    graph["entity_locations"][entity_id] = location
    if entity_id == "player":
        graph["current_area_id"] = area_id
    return graph


def list_entities_in_area(graph: Dict[str, Any], area_id: str) -> List[Dict[str, Any]]:
    graph = normalize_spatial_graph(graph)
    results = []
    for entity_id, location in graph["entity_locations"].items():
        if location.get("area_id") == area_id:
            results.append(dict(location, entity_id=entity_id))
    return results