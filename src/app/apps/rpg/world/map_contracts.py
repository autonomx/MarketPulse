"""Versioned deterministic contracts for interactive RPG maps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Sequence

MAP_SCHEMA_VERSION = 1
MapLevel = Literal["world", "region", "settlement", "dungeon", "interior", "encounter"]
MapAvailability = Literal["ready", "unavailable", "stale", "error"]
MapLayer = Literal[
    "background",
    "terrain",
    "routes",
    "ground_props",
    "structures",
    "markers",
    "labels",
    "fog",
    "interaction",
]
MapObjectKind = Literal[
    "building",
    "landmark",
    "gate",
    "prop",
    "vegetation",
    "resource",
    "entrance",
    "decorative",
]
MapActionType = Literal["travel", "inspect", "enter", "talk", "trade"]
RouteOverlayStatus = Literal["open", "blocked", "locked", "unknown"]
Point = tuple[int, int]

_LAYER_PRIORITY: dict[MapLayer, int] = {
    "background": 0,
    "terrain": 10,
    "routes": 20,
    "ground_props": 30,
    "structures": 40,
    "markers": 50,
    "labels": 60,
    "fog": 70,
    "interaction": 80,
}


class MapContractError(ValueError):
    """Typed deterministic map contract validation failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


@dataclass(frozen=True)
class MapBounds:
    width: int
    height: int
    x: int = 0
    y: int = 0

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise MapContractError("invalid_map_bounds")

    def contains(self, point: Point) -> bool:
        px, py = point
        return self.x <= px <= self.x + self.width and self.y <= py <= self.y + self.height


@dataclass(frozen=True)
class MapPolygon:
    points: tuple[Point, ...]
    kind: Literal["polygon"] = "polygon"

    def __post_init__(self) -> None:
        normalized = normalize_polygon(self.points)
        object.__setattr__(self, "points", normalized)


@dataclass(frozen=True)
class MapBackground:
    asset_id: str
    destination_bounds: MapBounds
    source_crop: MapBounds | None = None

    def __post_init__(self) -> None:
        _require_id(self.asset_id, "missing_background_asset_id")


@dataclass(frozen=True)
class MapSprite:
    asset_id: str
    width: int
    height: int

    def __post_init__(self) -> None:
        _require_id(self.asset_id, "missing_sprite_asset_id")
        if self.width <= 0 or self.height <= 0:
            raise MapContractError("invalid_sprite_dimensions", self.asset_id)


@dataclass(frozen=True)
class MapRenderOrder:
    layer: MapLayer
    sort_y: int
    offset: int = 0

    def __post_init__(self) -> None:
        if abs(self.offset) > 1000:
            raise MapContractError("render_offset_out_of_range", str(self.offset))

    def key(self, object_id: str) -> tuple[int, int, int, str]:
        return (_LAYER_PRIORITY[self.layer], self.sort_y, self.offset, object_id)


@dataclass(frozen=True)
class MapObjectDefinition:
    id: str
    kind: MapObjectKind
    x: int
    y: int
    render_order: MapRenderOrder
    location_id: str | None = None
    anchor: Literal["bottom_center", "center", "top_left"] = "bottom_center"
    sprite: MapSprite | None = None
    footprint: MapPolygon | None = None
    hitbox: MapPolygon | None = None
    child_map_id: str | None = None
    label: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_id(self.id, "missing_map_object_id")
        if self.location_id is not None:
            _require_id(self.location_id, "invalid_location_id")
        if self.child_map_id is not None:
            _require_id(self.child_map_id, "invalid_child_map_id")
        if self.kind != "decorative" and self.hitbox is None:
            raise MapContractError("interactive_object_missing_hitbox", self.id)

    @property
    def anchor_point(self) -> Point:
        return (self.x, self.y)


@dataclass(frozen=True)
class MapRouteGeometry:
    route_id: str
    points: tuple[Point, ...]
    style: str = "road"

    def __post_init__(self) -> None:
        _require_id(self.route_id, "missing_route_geometry_id")
        if len(self.points) < 2:
            raise MapContractError("route_geometry_too_short", self.route_id)
        if any(not _is_point(point) for point in self.points):
            raise MapContractError("invalid_route_geometry_point", self.route_id)


@dataclass(frozen=True)
class MapLabelDefinition:
    id: str
    text: str
    x: int
    y: int
    priority: int = 0

    def __post_init__(self) -> None:
        _require_id(self.id, "missing_map_label_id")
        if not self.text.strip():
            raise MapContractError("missing_map_label_text", self.id)


@dataclass(frozen=True)
class MapDefinition:
    map_id: str
    level: MapLevel
    bounds: MapBounds
    objects: tuple[MapObjectDefinition, ...] = ()
    route_geometry: tuple[MapRouteGeometry, ...] = ()
    labels: tuple[MapLabelDefinition, ...] = ()
    background: MapBackground | None = None
    parent_map_id: str | None = None
    seed: int = 0
    schema_version: int = MAP_SCHEMA_VERSION
    definition_revision: str = ""

    def __post_init__(self) -> None:
        _require_id(self.map_id, "missing_map_id")
        if self.schema_version != MAP_SCHEMA_VERSION:
            raise MapContractError("unsupported_map_schema_version", str(self.schema_version))
        if self.parent_map_id is not None:
            _require_id(self.parent_map_id, "invalid_parent_map_id")
        _require_unique((item.id for item in self.objects), "duplicate_map_object_id")
        _require_unique((item.id for item in self.labels), "duplicate_map_label_id")
        _require_unique((item.route_id for item in self.route_geometry), "duplicate_route_geometry_id")
        for item in self.objects:
            if not self.bounds.contains(item.anchor_point):
                raise MapContractError("map_object_out_of_bounds", item.id)
        for route in self.route_geometry:
            if any(not self.bounds.contains(point) for point in route.points):
                raise MapContractError("route_geometry_out_of_bounds", route.route_id)
        for label in self.labels:
            if not self.bounds.contains((label.x, label.y)):
                raise MapContractError("map_label_out_of_bounds", label.id)
        if self.background and self.background.destination_bounds != self.bounds:
            raise MapContractError("background_bounds_mismatch", self.map_id)

@dataclass(frozen=True)
class MapRouteOverlay:
    route_id: str
    status: RouteOverlayStatus
    known: bool = True
    safe: bool = True
    reason: str = ""

    def __post_init__(self) -> None:
        _require_id(self.route_id, "missing_route_overlay_id")


@dataclass(frozen=True)
class MapMarker:
    id: str
    kind: Literal["player", "quest", "danger", "event", "npc", "resource"]
    x: int
    y: int
    object_id: str | None = None
    label: str = ""

    def __post_init__(self) -> None:
        _require_id(self.id, "missing_map_marker_id")


@dataclass(frozen=True)
class MapActionCapability:
    type: MapActionType
    enabled: bool
    target_object_id: str
    target_location_id: str | None = None
    route_id: str | None = None
    disabled_reason: str = ""

    def __post_init__(self) -> None:
        _require_id(self.target_object_id, "missing_action_target_object_id")
        if not self.enabled and not self.disabled_reason:
            raise MapContractError("disabled_action_missing_reason", self.target_object_id)


@dataclass(frozen=True)
class MapOverlay:
    map_id: str
    session_id: str
    definition_revision: str
    overlay_revision: int
    session_turn_index: int
    availability: MapAvailability = "ready"
    unavailable_reason: str = ""
    current_location_id: str | None = None
    discovered_object_ids: tuple[str, ...] = ()
    visible_object_ids: tuple[str, ...] = ()
    routes: tuple[MapRouteOverlay, ...] = ()
    markers: tuple[MapMarker, ...] = ()
    capabilities: tuple[MapActionCapability, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_id(self.map_id, "missing_overlay_map_id")
        _require_id(self.session_id, "missing_overlay_session_id")
        if self.overlay_revision < 0 or self.session_turn_index < 0:
            raise MapContractError("negative_overlay_revision")
        if self.availability != "ready" and not self.unavailable_reason:
            raise MapContractError("unavailable_overlay_missing_reason")
        if self.availability == "ready" and not self.current_location_id:
            raise MapContractError("ready_overlay_missing_current_location")
        _require_unique(self.discovered_object_ids, "duplicate_discovered_object_id")
        _require_unique(self.visible_object_ids, "duplicate_visible_object_id")
        _require_unique((route.route_id for route in self.routes), "duplicate_route_overlay_id")
        _require_unique((marker.id for marker in self.markers), "duplicate_map_marker_id")


@dataclass(frozen=True)
class MapResourceEnvelope:
    map_id: str
    definition_revision: str
    overlay_revision: int
    session_turn_index: int
    definition: MapDefinition | None
    overlay: MapOverlay


def normalize_polygon(points: Sequence[Point]) -> tuple[Point, ...]:
    normalized = tuple((int(point[0]), int(point[1])) for point in points if _is_point(point))
    if len(normalized) > 1 and normalized[0] == normalized[-1]:
        normalized = normalized[:-1]
    if len(normalized) < 3 or len(set(normalized)) < 3:
        raise MapContractError("degenerate_polygon")
    if _polygon_area_twice(normalized) == 0:
        raise MapContractError("degenerate_polygon")
    if _has_self_intersection(normalized):
        raise MapContractError("self_intersecting_polygon")
    if _polygon_area_twice(normalized) > 0:
        normalized = tuple(reversed(normalized))
    return normalized


def _require_id(value: str, code: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MapContractError(code)


def _require_unique(values: Sequence[str] | object, code: str) -> None:
    seen: set[str] = set()
    for value in values:  # type: ignore[union-attr]
        if value in seen:
            raise MapContractError(code, value)
        seen.add(value)


def _is_point(value: object) -> bool:
    return isinstance(value, (tuple, list)) and len(value) == 2 and all(isinstance(item, int) and not isinstance(item, bool) for item in value)


def _polygon_area_twice(points: Sequence[Point]) -> int:
    return sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(points, (*points[1:], points[0]))
    )


def _orientation(a: Point, b: Point, c: Point) -> int:
    value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
    return 0 if value == 0 else (1 if value > 0 else -1)


def _point_on_segment(point: Point, left: Point, right: Point) -> bool:
    cross = (point[1] - left[1]) * (right[0] - left[0]) - (point[0] - left[0]) * (right[1] - left[1])
    return cross == 0 and min(left[0], right[0]) <= point[0] <= max(left[0], right[0]) and min(left[1], right[1]) <= point[1] <= max(left[1], right[1])


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    o1, o2, o3, o4 = _orientation(a, b, c), _orientation(a, b, d), _orientation(c, d, a), _orientation(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return any(
        orientation == 0 and _point_on_segment(point, left, right)
        for orientation, point, left, right in (
            (o1, c, a, b),
            (o2, d, a, b),
            (o3, a, c, d),
            (o4, b, c, d),
        )
    )


def _has_self_intersection(points: Sequence[Point]) -> bool:
    size = len(points)
    for index in range(size):
        a, b = points[index], points[(index + 1) % size]
        for other in range(index + 1, size):
            if other in {index, (index + 1) % size} or (other + 1) % size in {index, (index + 1) % size}:
                continue
            c, d = points[other], points[(other + 1) % size]
            if _segments_intersect(a, b, c, d):
                return True
    return False
