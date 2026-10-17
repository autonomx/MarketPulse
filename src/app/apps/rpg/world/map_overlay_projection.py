"""Typed discovery, fog, object-state, and environment projection for RPG maps."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from app.apps.rpg.world.map_contracts import MapDefinition, MapPolygon

ObjectMapStatus = Literal["normal", "open", "closed", "damaged", "burned", "occupied"]
_ALLOWED_OBJECT_STATUSES = {"normal", "open", "closed", "damaged", "burned", "occupied"}
_ALLOWED_ENVIRONMENT_KEYS = ("time", "weather", "temperature", "season", "light", "visibility")


@dataclass(frozen=True)
class MapObjectDynamicState:
    object_id: str
    discovered: bool
    visible: bool
    status: ObjectMapStatus = "normal"
    presentation_hint: str = ""


@dataclass(frozen=True)
class MapFogPolygon:
    id: str
    points: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        normalized = MapPolygon(points=self.points).points
        object.__setattr__(self, "points", normalized)


@dataclass(frozen=True)
class MapDynamicOverlay:
    object_states: tuple[MapObjectDynamicState, ...] = ()
    fog_polygons: tuple[MapFogPolygon, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def project_dynamic_map_overlay(session: Mapping[str, Any], definition: MapDefinition) -> MapDynamicOverlay:
    state = _mapping(session.get("state"))
    map_state = _mapping(state.get("map_state"))
    known_ids = {item.id for item in definition.objects}
    discovered = _known_ids(map_state.get("discovered_object_ids"), known_ids)
    visible = _known_ids(map_state.get("visible_object_ids"), discovered)
    raw_object_states = _mapping(map_state.get("object_states"))

    object_states = []
    for object_id in sorted(discovered):
        raw = _mapping(raw_object_states.get(object_id))
        status = str(raw.get("status") or "normal").strip().lower()
        if status not in _ALLOWED_OBJECT_STATUSES:
            status = "normal"
        object_states.append(
            MapObjectDynamicState(
                object_id=object_id,
                discovered=True,
                visible=object_id in visible,
                status=status,  # type: ignore[arg-type]
                presentation_hint=_safe_hint(raw.get("presentation_hint")),
            )
        )

    fog_polygons = []
    for index, polygon_raw in enumerate(_sequence(map_state.get("fog_polygons"))):
        item = _mapping(polygon_raw)
        polygon_id = str(item.get("id") or f"fog:{index}").strip()
        points = _points(item.get("points"))
        if not polygon_id or len(points) < 3:
            continue
        try:
            fog_polygons.append(MapFogPolygon(polygon_id, points))
        except ValueError:
            continue

    world = _mapping(state.get("world"))
    environment = {
        key: str(value)
        for key in _ALLOWED_ENVIRONMENT_KEYS
        if (value := world.get(key)) is not None
    }
    return MapDynamicOverlay(
        object_states=tuple(object_states),
        fog_polygons=tuple(sorted(fog_polygons, key=lambda item: item.id)),
        environment=environment,
    )


def merge_dynamic_overlay_payload(
    base_overlay: Mapping[str, Any],
    dynamic: MapDynamicOverlay,
) -> dict[str, Any]:
    payload = dict(base_overlay)
    payload.update(dynamic.as_dict())
    return payload


def _safe_hint(value: object) -> str:
    text = str(value or "").strip()
    return text[:160]


def _known_ids(value: object, allowed: set[str]) -> set[str]:
    return {str(item) for item in _sequence(value) if str(item) in allowed}


def _points(value: object) -> tuple[tuple[int, int], ...]:
    points = []
    for raw in _sequence(value):
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) == 2:
            left, right = raw
            if isinstance(left, int) and isinstance(right, int):
                points.append((left, right))
    return tuple(points)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return ()
