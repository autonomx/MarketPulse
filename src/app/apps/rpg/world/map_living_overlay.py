"""Pure projection of explicit living-world map records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.apps.rpg.world.map_contracts import MapDefinition
from typing import Any, cast

_ALLOWED_KINDS = {"npc", "danger", "resource", "quest", "event"}
_MAX_MARKERS = 256


@dataclass(frozen=True)
class LivingMapProjection:
    markers: tuple[dict[str, Any], ...]


def project_living_map_markers(
    session: Mapping[str, Any],
    definition: MapDefinition,
) -> LivingMapProjection:
    state = _mapping(session.get("state"))
    map_state = _mapping(state.get("map_state"))
    discovered = _strings(map_state.get("discovered_object_ids"))
    visible = _strings(map_state.get("visible_object_ids"))
    by_id = {item.id: item for item in definition.objects}
    by_location = {item.location_id: item for item in definition.objects if item.location_id}
    rows: dict[str, dict[str, Any]] = {}
    for raw in _records(state.get("map_presence")):
        if _text(raw.get("map_id")) != definition.map_id:
            continue
        if raw.get("visible_to_player") is not True or raw.get("discovered") is False:
            continue
        kind = _text(raw.get("kind")).lower()
        if kind not in _ALLOWED_KINDS:
            continue
        marker_id = _first(raw.get("marker_id"), raw.get("id"))
        if not marker_id:
            continue
        object_id = _text(raw.get("object_id"))
        location_id = _text(raw.get("location_id"))
        item = by_id.get(object_id) or by_location.get(location_id)
        resolved_id = _text(getattr(item, "id", "")) or object_id
        if resolved_id and (resolved_id not in discovered or resolved_id not in visible):
            continue
        point = _point(raw, item)
        if point is None:
            continue
        rows[marker_id] = {
            "id": marker_id,
            "kind": kind,
            "x": point[0],
            "y": point[1],
            "object_id": resolved_id or None,
            "label": _text(raw.get("label"))[:80],
        }
        if len(rows) >= _MAX_MARKERS:
            break
    return LivingMapProjection(tuple(rows[key] for key in sorted(rows)))


def _point(raw: Mapping[str, Any], item: object | None) -> tuple[int, int] | None:
    x, y = raw.get("x"), raw.get("y")
    if _integer(x) and _integer(y):
        return int(cast(Any, x)), int(cast(Any, y))
    if item is not None:
        x, y = getattr(item, "x", None), getattr(item, "y", None)
        if _integer(x) and _integer(y):
            return int(cast(Any, x)), int(cast(Any, y))
    return None


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


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
