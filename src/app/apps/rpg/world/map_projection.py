"""Lossless read-only projection from authoritative RPG session state to map overlays."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.apps.rpg.world.map_contracts import (
    MapActionCapability,
    MapMarker,
    MapOverlay,
    MapRouteOverlay,
)
from app.apps.rpg.world.map_fixtures import FROST_HAVEN_MAP_ID, NORTHERN_PASS_MAP_ID
from app.apps.rpg.world.map_repository import MapDefinitionRepository, default_map_repository
from app.apps.rpg.world.map_world_integration import (
    canonical_route_id_for_locations,
    map_repository_for_session,
)

MAP_STATE_SCHEMA_VERSION = 1

_STARTING_MAP_BY_LOCATION = {
    "rusty_flagon_tavern": FROST_HAVEN_MAP_ID,
    "market_district": FROST_HAVEN_MAP_ID,
    "frost_haven": FROST_HAVEN_MAP_ID,
    "northern_road": NORTHERN_PASS_MAP_ID,
    "glimmerdeep_pass": NORTHERN_PASS_MAP_ID,
    "old_quarry": NORTHERN_PASS_MAP_ID,
    "frostpine_hollow": NORTHERN_PASS_MAP_ID,
    "northern_watchtower": NORTHERN_PASS_MAP_ID,
}


def initial_map_session_state(
    starting_location_id: str,
    repository: MapDefinitionRepository | None = None,
) -> dict[str, Any]:
    """Create explicit deterministic map state for a newly created campaign."""

    repository = repository or default_map_repository()
    map_id = _STARTING_MAP_BY_LOCATION.get(starting_location_id, NORTHERN_PASS_MAP_ID)
    definition = repository.get(map_id)
    visible = [item.id for item in definition.objects]
    route_states = {
        route.route_id: {"status": "open", "known": True, "safe": True}
        for route in definition.route_geometry
    }
    return {
        "schema_version": MAP_STATE_SCHEMA_VERSION,
        "current_map_id": map_id,
        "current_location_id": starting_location_id,
        "overlay_revision": 0,
        "discovered_object_ids": visible,
        "visible_object_ids": visible,
        "route_states": route_states,
        "object_states": {},
        "map_history": [map_id],
    }


def project_session_map_overlay(
    session: Mapping[str, Any],
    map_id: str,
    repository: MapDefinitionRepository | None = None,
) -> MapOverlay:
    repository = repository or map_repository_for_session(session)
    definition = repository.get(map_id)
    state = _mapping(session.get("state"))
    manifest = _mapping(session.get("manifest"))
    session_id = str(manifest.get("session_id") or manifest.get("id") or state.get("session_id") or "")
    turn_index = _non_negative_int(state.get("current_turn") or state.get("turn_count"))
    map_state = _mapping(state.get("map_state"))
    overlay_revision = _non_negative_int(map_state.get("overlay_revision"), fallback=turn_index)

    if not session_id:
        return _unavailable_overlay(
            map_id,
            "unknown-session",
            definition.definition_revision,
            overlay_revision,
            turn_index,
            "session_id_unavailable",
        )
    if not map_state:
        return _unavailable_overlay(
            map_id,
            session_id,
            definition.definition_revision,
            overlay_revision,
            turn_index,
            "map_state_unavailable",
        )
    if _non_negative_int(map_state.get("schema_version")) != MAP_STATE_SCHEMA_VERSION:
        return _unavailable_overlay(
            map_id,
            session_id,
            definition.definition_revision,
            overlay_revision,
            turn_index,
            "map_state_schema_unsupported",
        )

    current_map_id = _text(map_state.get("current_map_id"))
    current_location_id = _text(map_state.get("current_location_id"))
    if not current_map_id or not current_location_id:
        return _unavailable_overlay(
            map_id,
            session_id,
            definition.definition_revision,
            overlay_revision,
            turn_index,
            "current_location_unavailable",
        )
    if current_map_id != map_id:
        return _unavailable_overlay(
            map_id,
            session_id,
            definition.definition_revision,
            overlay_revision,
            turn_index,
            "map_not_active",
        )

    player_object = next(
        (item for item in definition.objects if item.location_id == current_location_id),
        None,
    )
    if player_object is None:
        return _unavailable_overlay(
            map_id,
            session_id,
            definition.definition_revision,
            overlay_revision,
            turn_index,
            "current_location_not_in_definition",
        )

    discovered = _known_ids(map_state.get("discovered_object_ids"), {item.id for item in definition.objects})
    visible = _known_ids(map_state.get("visible_object_ids"), set(discovered))
    routes = _route_overlays(definition.route_geometry, _mapping(map_state.get("route_states")))
    markers = _markers(state, player_object.id, player_object.x, player_object.y, visible)
    capabilities = _capabilities(session, definition.objects, visible, current_location_id, routes)
    environment = _environment(state)
    return MapOverlay(
        map_id=map_id,
        session_id=session_id,
        definition_revision=definition.definition_revision,
        overlay_revision=overlay_revision,
        session_turn_index=turn_index,
        current_location_id=current_location_id,
        discovered_object_ids=tuple(sorted(discovered)),
        visible_object_ids=tuple(sorted(visible)),
        routes=routes,
        markers=markers,
        capabilities=capabilities,
        environment=environment,
    )


def increment_map_overlay_revision(state: dict[str, Any]) -> int:
    """Advance the persisted overlay revision after an authoritative mutation."""

    map_state = raw_map_state if isinstance(raw_map_state := state.get("map_state"), dict) else {}
    revision = _non_negative_int(map_state.get("overlay_revision")) + 1
    map_state["overlay_revision"] = revision
    state["map_state"] = map_state
    return revision


def _unavailable_overlay(
    map_id: str,
    session_id: str,
    definition_revision: str,
    overlay_revision: int,
    turn_index: int,
    reason: str,
) -> MapOverlay:
    return MapOverlay(
        map_id=map_id,
        session_id=session_id,
        definition_revision=definition_revision,
        overlay_revision=overlay_revision,
        session_turn_index=turn_index,
        availability="unavailable",
        unavailable_reason=reason,
    )


def _route_overlays(route_geometry: Sequence[Any], route_states: Mapping[str, Any]) -> tuple[MapRouteOverlay, ...]:
    overlays = []
    for route in route_geometry:
        route_id = str(getattr(route, "route_id"))
        state = _mapping(route_states.get(route_id))
        status = _text(state.get("status")) or "open"
        if status not in {"open", "blocked", "locked", "unknown"}:
            status = "unknown"
        overlays.append(
            MapRouteOverlay(
                route_id=route_id,
                status=status,  # type: ignore[arg-type]
                known=bool(state.get("known", True)),
                safe=bool(state.get("safe", True)),
                reason=_text(state.get("reason")),
            )
        )
    return tuple(overlays)


def _markers(
    state: Mapping[str, Any],
    player_object_id: str,
    x: int,
    y: int,
    visible: set[str],
) -> tuple[MapMarker, ...]:
    markers = [MapMarker("marker:player", "player", x, y, object_id=player_object_id, label="You")]
    map_state = _mapping(state.get("map_state"))
    for raw in _sequence(map_state.get("markers")):
        marker = _mapping(raw)
        object_id = _text(marker.get("object_id")) or None
        if object_id and object_id not in visible:
            continue
        kind = _text(marker.get("kind"))
        if kind not in {"quest", "danger", "event", "npc", "resource"}:
            continue
        marker_id = _text(marker.get("id"))
        if not marker_id:
            continue
        markers.append(
            MapMarker(
                marker_id,
                kind,  # type: ignore[arg-type]
                int(marker.get("x") or 0),
                int(marker.get("y") or 0),
                object_id=object_id,
                label=_text(marker.get("label")),
            )
        )
    return tuple(sorted(markers, key=lambda item: item.id))


def _capabilities(
    session: Mapping[str, Any],
    objects: Sequence[Any],
    visible: set[str],
    current_location_id: str,
    routes: tuple[MapRouteOverlay, ...],
) -> tuple[MapActionCapability, ...]:
    routes_by_id = {route.route_id: route for route in routes}
    capabilities: list[MapActionCapability] = []
    for item in objects:
        object_id = str(getattr(item, "id"))
        location_id = getattr(item, "location_id")
        if object_id not in visible or not location_id:
            continue
        capabilities.append(
            MapActionCapability(
                type="inspect",
                enabled=True,
                target_object_id=object_id,
                target_location_id=str(location_id),
            )
        )
        if str(location_id) == current_location_id:
            continue
        route_id = canonical_route_id_for_locations(session, current_location_id, str(location_id))
        route_id = route_id or _route_id_for_object(item, routes_by_id)
        route = routes_by_id.get(route_id) if route_id else None
        enabled = route is None or (route.known and route.status == "open" and route.safe)
        reason = "" if enabled else _route_disabled_reason(route)
        capabilities.append(
            MapActionCapability(
                type="travel",
                enabled=enabled,
                target_object_id=object_id,
                target_location_id=str(location_id),
                route_id=route_id,
                disabled_reason=reason,
            )
        )
    return tuple(sorted(capabilities, key=lambda item: (item.target_object_id, item.type)))


def _route_id_for_object(item: object, routes: Mapping[str, MapRouteOverlay]) -> str | None:
    tags = tuple(str(tag) for tag in getattr(item, "tags", ()))
    tagged = next((tag.removeprefix("route_id:") for tag in tags if tag.startswith("route_id:")), None)
    if tagged in routes:
        return tagged
    object_id = str(getattr(item, "id", ""))
    token = object_id.split(":")[-1]
    return next((route_id for route_id in sorted(routes) if token in route_id), None)


def _route_disabled_reason(route: MapRouteOverlay | None) -> str:
    if route is None:
        return "route_unavailable"
    if not route.known:
        return "route_unknown"
    if route.status != "open":
        return f"route_{route.status}"
    if not route.safe:
        return "route_requires_encounter_check"
    return "route_unavailable"


def _environment(state: Mapping[str, Any]) -> dict[str, str]:
    world = _mapping(state.get("world"))
    return {
        key: str(value)
        for key in ("time", "weather", "temperature")
        if (value := world.get(key)) is not None
    }


def _known_ids(value: object, allowed: set[str]) -> set[str]:
    return {str(item) for item in _sequence(value) if str(item) in allowed}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return ()


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _non_negative_int(value: Any, *, fallback: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, fallback)
