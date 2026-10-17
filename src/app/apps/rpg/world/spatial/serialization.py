from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    return default


def normalize_spatial_graph(value: Dict[str, Any] | None) -> Dict[str, Any]:
    """Return a save/load-safe spatial graph dict.

    This is intentionally tolerant so older sessions without spatial data do not
    crash.  The returned object is always JSON-serializable and mutable.
    """
    source = _safe_dict(value)

    areas: Dict[str, Dict[str, Any]] = {}
    for area_id, area in _safe_dict(source.get("areas")).items():
        area = _safe_dict(area)
        normalized_id = _safe_str(area.get("area_id")) or _safe_str(area_id)
        if not normalized_id:
            continue
        areas[normalized_id] = {
            "area_id": normalized_id,
            "name": _safe_str(area.get("name")) or normalized_id,
            "description": _safe_str(area.get("description")),
            "tags": list(area.get("tags") or []),
        }

    connections: Dict[str, Dict[str, Any]] = {}
    for connection_id, connection in _safe_dict(source.get("connections")).items():
        connection = _safe_dict(connection)
        normalized_id = (
            _safe_str(connection.get("connection_id")) or _safe_str(connection_id)
        )
        from_area_id = _safe_str(connection.get("from_area_id"))
        to_area_id = _safe_str(connection.get("to_area_id"))
        if not normalized_id or not from_area_id or not to_area_id:
            continue
        connections[normalized_id] = {
            "connection_id": normalized_id,
            "from_area_id": from_area_id,
            "to_area_id": to_area_id,
            "label": _safe_str(connection.get("label")),
            "bidirectional": _safe_bool(connection.get("bidirectional"), True),
            "barrier_kind": _safe_str(connection.get("barrier_kind")) or "none",
            "is_open": _safe_bool(connection.get("is_open"), True),
            "is_locked": _safe_bool(connection.get("is_locked"), False),
            "blocks_movement": _safe_bool(connection.get("blocks_movement"), False),
            "visibility": _safe_str(connection.get("visibility")) or "open",
            "audibility": _safe_str(connection.get("audibility")) or "open",
            "metadata": dict(_safe_dict(connection.get("metadata"))),
        }

    entity_locations: Dict[str, Dict[str, Any]] = {}
    for entity_id, location in _safe_dict(source.get("entity_locations")).items():
        location = _safe_dict(location)
        normalized_entity_id = _safe_str(location.get("entity_id")) or _safe_str(entity_id)
        area_id = _safe_str(location.get("area_id"))
        if not normalized_entity_id or not area_id:
            continue
        entity_locations[normalized_entity_id] = {
            "entity_id": normalized_entity_id,
            "area_id": area_id,
            "hidden": _safe_bool(location.get("hidden"), False),
            "silent": _safe_bool(location.get("silent"), False),
            "tags": list(location.get("tags") or []),
        }

    current_area_id = _safe_str(source.get("current_area_id"))
    if not current_area_id:
        player_location = entity_locations.get("player") or {}
        current_area_id = _safe_str(player_location.get("area_id"))
    if not current_area_id and areas:
        current_area_id = sorted(areas)[0]

    return {
        "graph_id": _safe_str(source.get("graph_id")) or "spatial_graph",
        "current_area_id": current_area_id,
        "areas": areas,
        "connections": connections,
        "entity_locations": entity_locations,
        "metadata": dict(_safe_dict(source.get("metadata"))),
    }