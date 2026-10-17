"""Pure projection of authoritative map object, route, and environment state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.apps.rpg.world.map_contracts import MapDefinition
from typing import Any

_OBJECT_STATUSES = {"normal", "open", "closed", "damaged", "burned", "occupied"}
_ROUTE_STATUSES = {"open", "blocked", "locked", "unknown"}


@dataclass(frozen=True)
class LivingMapStateProjection:
    object_states: tuple[dict[str, Any], ...]
    routes: tuple[dict[str, Any], ...]
    environment: Mapping[str, str]


def project_living_map_state(
    session: Mapping[str, Any],
    definition: MapDefinition,
) -> LivingMapStateProjection:
    state = _mapping(session.get("state"))
    map_state = _mapping(state.get("map_state"))
    return LivingMapStateProjection(
        object_states=_object_states(state, definition, map_state),
        routes=_route_states(state, definition),
        environment=_environment(state),
    )


def merge_living_overlay_payload(
    base: Mapping[str, Any],
    markers: Sequence[Mapping[str, Any]],
    living: LivingMapStateProjection,
) -> dict[str, Any]:
    payload = dict(base)
    payload["markers"] = _merge(base.get("markers"), markers, "id")
    payload["object_states"] = _merge(base.get("object_states"), living.object_states, "object_id")
    payload["routes"] = _merge(base.get("routes"), living.routes, "route_id")
    payload["environment"] = dict(sorted({
        **_mapping(base.get("environment")),
        **living.environment,
    }.items()))
    return payload


def _object_states(
    state: Mapping[str, Any],
    definition: MapDefinition,
    map_state: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    allowed = {item.id for item in definition.objects}
    discovered = _strings(map_state.get("discovered_object_ids"))
    visible = _strings(map_state.get("visible_object_ids"))
    rows: dict[str, dict[str, Any]] = {}
    for raw in _records(state.get("map_object_states")):
        object_id = _text(raw.get("object_id"))
        if _text(raw.get("map_id")) != definition.map_id:
            continue
        if object_id not in allowed or object_id not in discovered:
            continue
        status = _first(raw.get("status"), "normal").lower()
        if status not in _OBJECT_STATUSES:
            status = "normal"
        rows[object_id] = {
            "object_id": object_id,
            "discovered": True,
            "visible": object_id in visible and raw.get("visible_to_player", True) is not False,
            "status": status,
            "presentation_hint": _text(raw.get("presentation_hint"))[:160],
        }
    return tuple(rows[key] for key in sorted(rows))


def _route_states(
    state: Mapping[str, Any],
    definition: MapDefinition,
) -> tuple[dict[str, Any], ...]:
    allowed = {item.route_id for item in definition.route_geometry}
    graph = _mapping(state.get("world_graph"))
    rows: dict[str, dict[str, Any]] = {}
    for raw in _records(graph.get("routes")):
        route_id = _first(raw.get("id"), raw.get("route_id"))
        if route_id not in allowed:
            continue
        status = _first(raw.get("status"), "open").lower()
        if status not in _ROUTE_STATUSES:
            status = "unknown"
        known = bool(raw.get("known", True))
        rows[route_id] = {
            "route_id": route_id,
            "status": status,
            "known": known,
            "safe": bool(raw.get("safe", True)),
            "reason": _text(raw.get("reason"))[:160] if known else "",
        }
    return tuple(rows[key] for key in sorted(rows))


def _environment(state: Mapping[str, Any]) -> dict[str, str]:
    world = _mapping(state.get("world"))
    climate = _mapping(state.get("climate"))
    values = {
        "time": world.get("time"),
        "weather": world.get("weather") or climate.get("weather"),
        "temperature": world.get("temperature") or climate.get("temperature"),
        "season": world.get("season") or climate.get("season"),
        "light": world.get("light"),
        "visibility": world.get("visibility") or climate.get("visibility"),
    }
    return {
        key: str(value)
        for key, value in values.items()
        if value is not None and str(value).strip()
    }


def _merge(base: object, additions: Sequence[Mapping[str, Any]], key_name: str) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for raw in (*_records(base), *tuple(additions)):
        key = _text(raw.get(key_name))
        if key:
            rows[key] = dict(raw)
    return [rows[key] for key in sorted(rows)]


def _records(value: object) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        return tuple(
            {"id": str(key), **dict(item)}
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if isinstance(item, Mapping)
        )
    return tuple(item for item in _sequence(value) if isinstance(item, Mapping))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> tuple[object, ...]:
    return tuple(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _strings(value: object) -> set[str]:
    return {str(item) for item in _sequence(value) if str(item).strip()}


def _first(*values: object) -> str:
    return next((str(value).strip() for value in values if value is not None and str(value).strip()), "")


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
