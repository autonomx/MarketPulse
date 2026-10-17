from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.world.spatial.graph import find_connection, get_entity_area
from app.apps.rpg.world.spatial.serialization import normalize_spatial_graph


def can_hear_area(
    graph: Dict[str, Any],
    listener_area_id: str,
    source_area_id: str,
    *,
    sound_level: str = "normal",
) -> Dict[str, Any]:
    graph = normalize_spatial_graph(graph)

    if not listener_area_id or not source_area_id:
        return {
            "ok": False,
            "audibility": "blocked",
            "reason": "unknown_area",
            "listener_area_id": listener_area_id,
            "source_area_id": source_area_id,
            "connection_id": "",
            "barrier_kind": "",
            "sound_level": sound_level,
        }

    if listener_area_id not in graph.get("areas", {}) or source_area_id not in graph.get("areas", {}):
        return {
            "ok": False,
            "audibility": "blocked",
            "reason": "unknown_area",
            "listener_area_id": listener_area_id,
            "source_area_id": source_area_id,
            "connection_id": "",
            "barrier_kind": "",
            "sound_level": sound_level,
        }

    if listener_area_id == source_area_id:
        return {
            "ok": True,
            "audibility": "open",
            "reason": "same_area",
            "listener_area_id": listener_area_id,
            "source_area_id": source_area_id,
            "connection_id": "",
            "barrier_kind": "",
            "sound_level": sound_level,
        }

    connection = find_connection(graph, listener_area_id, source_area_id)
    if not connection:
        return {
            "ok": False,
            "audibility": "blocked",
            "reason": "out_of_range",
            "listener_area_id": listener_area_id,
            "source_area_id": source_area_id,
            "connection_id": "",
            "barrier_kind": "",
            "sound_level": sound_level,
        }

    barrier_kind = str(connection.get("barrier_kind") or "none")
    audibility = str(connection.get("audibility") or "open")
    is_open = bool(connection.get("is_open", True))

    if barrier_kind == "wall" or audibility == "blocked":
        ok = False
        reason = "blocked_by_barrier"
        final_audibility = "blocked"
    elif audibility == "muffled":
        ok = True
        reason = "muffled_by_barrier"
        final_audibility = "muffled"
    elif barrier_kind in {"door", "gate", "portcullis"} and not is_open:
        ok = True
        reason = "muffled_by_barrier"
        final_audibility = "muffled"
    else:
        ok = True
        reason = "audible_connection"
        final_audibility = "open"

    if final_audibility == "muffled" and sound_level == "quiet":
        ok = False

    return {
        "ok": ok,
        "audibility": final_audibility,
        "reason": reason,
        "listener_area_id": listener_area_id,
        "source_area_id": source_area_id,
        "connection_id": str(connection.get("connection_id") or ""),
        "barrier_kind": barrier_kind,
        "sound_level": sound_level,
    }


def can_hear_entity(
    graph: Dict[str, Any],
    listener_entity_id: str,
    source_entity_id: str,
    *,
    sound_level: str = "normal",
) -> Dict[str, Any]:
    graph = normalize_spatial_graph(graph)
    listener_location = graph["entity_locations"].get(listener_entity_id) or {}
    source_location = graph["entity_locations"].get(source_entity_id) or {}

    if not listener_location or not source_location:
        return {
            "ok": False,
            "audibility": "blocked",
            "reason": "unknown_entity",
            "listener_entity_id": listener_entity_id,
            "source_entity_id": source_entity_id,
            "listener_area_id": str(listener_location.get("area_id") or ""),
            "source_area_id": str(source_location.get("area_id") or ""),
            "connection_id": "",
            "barrier_kind": "",
            "sound_level": sound_level,
        }

    if bool(source_location.get("silent")) and sound_level != "loud":
        return {
            "ok": False,
            "audibility": "blocked",
            "reason": "silent",
            "listener_entity_id": listener_entity_id,
            "source_entity_id": source_entity_id,
            "listener_area_id": get_entity_area(graph, listener_entity_id),
            "source_area_id": str(source_location.get("area_id") or ""),
            "connection_id": "",
            "barrier_kind": "",
            "sound_level": sound_level,
        }

    listener_area_id = get_entity_area(graph, listener_entity_id)
    source_area_id = str(source_location.get("area_id") or "")
    result = can_hear_area(
        graph,
        listener_area_id,
        source_area_id,
        sound_level=sound_level,
    )
    result["listener_entity_id"] = listener_entity_id
    result["source_entity_id"] = source_entity_id
    return result


def audible_entities_from(
    graph: Dict[str, Any],
    listener_entity_id: str,
) -> List[Dict[str, Any]]:
    graph = normalize_spatial_graph(graph)
    results: List[Dict[str, Any]] = []
    for entity_id in sorted(graph["entity_locations"]):
        if entity_id == listener_entity_id:
            continue
        check = can_hear_entity(graph, listener_entity_id, entity_id)
        if check.get("ok"):
            location = dict(graph["entity_locations"].get(entity_id) or {})
            results.append(
                {
                    "entity_id": entity_id,
                    "area_id": location.get("area_id"),
                    "audibility": check.get("audibility"),
                    "reason": check.get("reason"),
                }
            )
    return results