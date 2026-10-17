from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.world.spatial.graph import find_connection, get_entity_area, set_entity_area
from app.apps.rpg.world.spatial.serialization import normalize_spatial_graph


def can_move_between(
    graph: Dict[str, Any],
    from_area_id: str,
    to_area_id: str,
) -> Dict[str, Any]:
    graph = normalize_spatial_graph(graph)

    if not from_area_id or not to_area_id:
        return {
            "ok": False,
            "from_area_id": from_area_id,
            "to_area_id": to_area_id,
            "connection_id": "",
            "reason": "unknown_area",
            "barrier_kind": "",
        }

    if from_area_id not in graph.get("areas", {}) or to_area_id not in graph.get("areas", {}):
        return {
            "ok": False,
            "from_area_id": from_area_id,
            "to_area_id": to_area_id,
            "connection_id": "",
            "reason": "unknown_area",
            "barrier_kind": "",
        }
    connection = find_connection(graph, from_area_id, to_area_id)
    if not connection:
        return {
            "ok": False,
            "from_area_id": from_area_id,
            "to_area_id": to_area_id,
            "connection_id": "",
            "reason": "no_connection",
            "barrier_kind": "",
        }

    barrier_kind = str(connection.get("barrier_kind") or "none")
    is_open = bool(connection.get("is_open", True))
    is_locked = bool(connection.get("is_locked", False))

    if barrier_kind == "wall":
        reason = "blocked"
    elif barrier_kind == "locked_door" or is_locked:
        reason = "locked"
    elif bool(connection.get("blocks_movement")):
        reason = "blocked"
    elif barrier_kind in {"door", "gate", "portcullis"} and not is_open:
        reason = "closed"
    else:
        reason = "passable"

    return {
        "ok": reason == "passable",
        "from_area_id": from_area_id,
        "to_area_id": to_area_id,
        "connection_id": str(connection.get("connection_id") or ""),
        "reason": reason,
        "barrier_kind": barrier_kind,
        "is_open": is_open,
        "is_locked": is_locked,
    }


def move_entity(
    graph: Dict[str, Any],
    entity_id: str,
    to_area_id: str,
) -> Dict[str, Any]:
    graph = normalize_spatial_graph(graph)
    from_area_id = get_entity_area(graph, entity_id)
    movement = can_move_between(graph, from_area_id, to_area_id)
    movement["entity_id"] = entity_id
    movement["moved"] = False

    if not movement.get("ok"):
        return movement

    set_entity_area(graph, entity_id, to_area_id)
    movement["moved"] = True
    return movement