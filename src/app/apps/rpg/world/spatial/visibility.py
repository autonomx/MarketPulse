from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.world.spatial.graph import (
    find_connection,
    get_entity_area,
)
from app.apps.rpg.world.spatial.serialization import normalize_spatial_graph


def can_see_area(
    graph: Dict[str, Any],
    viewer_area_id: str,
    target_area_id: str,
) -> Dict[str, Any]:
    graph = normalize_spatial_graph(graph)

    if not viewer_area_id or not target_area_id:
        return {
            "ok": False,
            "visibility": "blocked",
            "reason": "unknown_area",
            "viewer_area_id": viewer_area_id,
            "target_area_id": target_area_id,
            "connection_id": "",
            "barrier_kind": "",
        }

    if viewer_area_id not in graph.get("areas", {}) or target_area_id not in graph.get("areas", {}):
        return {
            "ok": False,
            "visibility": "blocked",
            "reason": "unknown_area",
            "viewer_area_id": viewer_area_id,
            "target_area_id": target_area_id,
            "connection_id": "",
            "barrier_kind": "",
        }

    if viewer_area_id == target_area_id:
        return {
            "ok": True,
            "visibility": "open",
            "reason": "same_area",
            "viewer_area_id": viewer_area_id,
            "target_area_id": target_area_id,
            "connection_id": "",
            "barrier_kind": "",
        }

    connection = find_connection(graph, viewer_area_id, target_area_id)
    if not connection:
        return {
            "ok": False,
            "visibility": "blocked",
            "reason": "out_of_range",
            "viewer_area_id": viewer_area_id,
            "target_area_id": target_area_id,
            "connection_id": "",
            "barrier_kind": "",
        }

    barrier_kind = str(connection.get("barrier_kind") or "none")
    visibility = str(connection.get("visibility") or "open")
    is_open = bool(connection.get("is_open", True))

    if barrier_kind == "wall" or visibility == "blocked":
        ok = False
        reason = "blocked_by_barrier"
        final_visibility = "blocked"
    elif barrier_kind in {"door", "gate", "portcullis"} and not is_open:
        ok = False
        reason = "blocked_by_barrier"
        final_visibility = "blocked"
    elif visibility == "partial" or barrier_kind in {"window", "curtain"}:
        ok = True
        reason = "visible_connection"
        final_visibility = "partial"
    else:
        ok = True
        reason = "visible_connection"
        final_visibility = "open"

    return {
        "ok": ok,
        "visibility": final_visibility,
        "reason": reason,
        "viewer_area_id": viewer_area_id,
        "target_area_id": target_area_id,
        "connection_id": str(connection.get("connection_id") or ""),
        "barrier_kind": barrier_kind,
    }


def can_see_entity(
    graph: Dict[str, Any],
    viewer_entity_id: str,
    target_entity_id: str,
) -> Dict[str, Any]:
    graph = normalize_spatial_graph(graph)
    viewer_location = graph["entity_locations"].get(viewer_entity_id) or {}
    target_location = graph["entity_locations"].get(target_entity_id) or {}

    if not viewer_location or not target_location:
        return {
            "ok": False,
            "visibility": "blocked",
            "reason": "unknown_entity",
            "viewer_entity_id": viewer_entity_id,
            "target_entity_id": target_entity_id,
            "viewer_area_id": str(viewer_location.get("area_id") or ""),
            "target_area_id": str(target_location.get("area_id") or ""),
            "connection_id": "",
            "barrier_kind": "",
        }

    if bool(target_location.get("hidden")):
        return {
            "ok": False,
            "visibility": "blocked",
            "reason": "hidden",
            "viewer_entity_id": viewer_entity_id,
            "target_entity_id": target_entity_id,
            "viewer_area_id": get_entity_area(graph, viewer_entity_id),
            "target_area_id": str(target_location.get("area_id") or ""),
            "connection_id": "",
            "barrier_kind": "",
        }

    viewer_area_id = get_entity_area(graph, viewer_entity_id)
    target_area_id = str(target_location.get("area_id") or "")
    result = can_see_area(graph, viewer_area_id, target_area_id)
    result["viewer_entity_id"] = viewer_entity_id
    result["target_entity_id"] = target_entity_id
    return result


def visible_entities_from(
    graph: Dict[str, Any],
    viewer_entity_id: str,
) -> List[Dict[str, Any]]:
    graph = normalize_spatial_graph(graph)
    results: List[Dict[str, Any]] = []
    for entity_id in sorted(graph["entity_locations"]):
        if entity_id == viewer_entity_id:
            continue
        check = can_see_entity(graph, viewer_entity_id, entity_id)
        if check.get("ok"):
            location = dict(graph["entity_locations"].get(entity_id) or {})
            results.append(
                {
                    "entity_id": entity_id,
                    "area_id": location.get("area_id"),
                    "visibility": check.get("visibility"),
                    "reason": check.get("reason"),
                }
            )
    return results