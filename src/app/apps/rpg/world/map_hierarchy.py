"""Deterministic parent/child map state switching helpers."""

from __future__ import annotations

from typing import Any

from app.apps.rpg.world.map_repository import MapDefinitionRepository

_OVERLAY_KEYS = (
    "discovered_object_ids",
    "visible_object_ids",
    "route_states",
    "object_states",
    "fog_polygons",
    "markers",
    "overlay_revision",
)


def switch_active_map(
    state: dict[str, Any],
    destination_map_id: str,
    preferred_location_id: str | None,
    repository: MapDefinitionRepository,
) -> tuple[dict[str, Any], str]:
    map_state = raw_map_state if isinstance(raw_map_state := state.get("map_state"), dict) else {}
    current_map_id = str(map_state.get("current_map_id") or "")
    overlays = raw_map_overlays if isinstance(raw_map_overlays := map_state.get("map_overlays"), dict) else {}
    if current_map_id:
        overlays[current_map_id] = {
            key: map_state.get(key)
            for key in _OVERLAY_KEYS
            if key in map_state
        }

    destination = repository.get(destination_map_id)
    restored = raw_restored if isinstance(raw_restored := overlays.get(destination_map_id), dict) else {}
    destination_location_id = _location_for_map(destination, preferred_location_id)
    map_state["current_map_id"] = destination_map_id
    map_state["current_location_id"] = destination_location_id
    map_state["discovered_object_ids"] = list(restored.get("discovered_object_ids") or [item.id for item in destination.objects])
    map_state["visible_object_ids"] = list(restored.get("visible_object_ids") or [item.id for item in destination.objects])
    map_state["route_states"] = dict(restored.get("route_states") or {
        route.route_id: {"status": "open", "known": True, "safe": True}
        for route in destination.route_geometry
    })
    map_state["object_states"] = dict(restored.get("object_states") or {})
    map_state["fog_polygons"] = list(restored.get("fog_polygons") or [])
    map_state["markers"] = list(restored.get("markers") or [])
    map_state["overlay_revision"] = int(restored.get("overlay_revision") or 0)
    map_state["map_overlays"] = overlays
    history = [str(item) for item in map_state.get("map_history", []) if str(item)]
    if not history or history[-1] != destination_map_id:
        history.append(destination_map_id)
    map_state["map_history"] = history[-24:]
    state["map_state"] = map_state
    return map_state, destination_location_id


def _location_for_map(definition: object, preferred_location_id: str | None) -> str:
    objects = tuple(getattr(definition, "objects", ()))
    if preferred_location_id and any(getattr(item, "location_id", None) == preferred_location_id for item in objects):
        return preferred_location_id
    entry = next(
        (
            item
            for item in objects
            if "entry" in getattr(item, "tags", ()) or "exit" in getattr(item, "tags", ())
        ),
        None,
    )
    if entry is not None and getattr(entry, "location_id", None):
        return str(entry.location_id)
    first = next((item for item in objects if getattr(item, "location_id", None)), None)
    if first is None:
        raise ValueError(f"map_has_no_location:{getattr(definition, 'map_id', 'unknown')}")
    return str(first.location_id)
