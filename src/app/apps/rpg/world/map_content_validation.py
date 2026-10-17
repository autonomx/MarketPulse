"""Validation helpers for editable RPG map definition content."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from app.apps.rpg.world.map_contracts import MapContractError
from app.apps.rpg.world.map_serialization import canonical_map_json, map_content_revision
from typing import Any


@dataclass(frozen=True)
class MapContentIssue:
    severity: str
    code: str
    path: str
    detail: str = ""


@dataclass(frozen=True)
class MapContentReport:
    ok: bool
    revision: str
    canonical_json: str
    issues: tuple[MapContentIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_map_content(
    definition: object,
    *,
    canonical_route_ids: Sequence[str] = (),
    known_map_ids: Sequence[str] = (),
    allowed_asset_ids: Sequence[str] = (),
) -> MapContentReport:
    issues: list[MapContentIssue] = []
    raw = _mapping(definition)
    if not raw:
        issues.append(MapContentIssue("error", "definition_required", "definition"))
        return _report({}, issues)

    _required_text(raw, "map_id", "definition.map_id", issues)
    level = _required_text(raw, "level", "definition.level", issues)
    if level and level not in {"world", "region", "settlement", "dungeon", "interior", "encounter"}:
        issues.append(MapContentIssue("error", "unsupported_map_level", "definition.level", level))
    bounds = _mapping(raw.get("bounds"))
    _positive_integer(bounds, "width", "definition.bounds.width", issues)
    _positive_integer(bounds, "height", "definition.bounds.height", issues)

    objects = _rows(raw.get("objects"))
    object_ids = _unique_ids(objects, "definition.objects", issues)
    for index, item in enumerate(objects):
        path = f"definition.objects[{index}]"
        _required_text(item, "kind", f"{path}.kind", issues)
        _integer(item, "x", f"{path}.x", issues)
        _integer(item, "y", f"{path}.y", issues)
        hitbox = item.get("hitbox")
        if item.get("kind") != "decorative" and not isinstance(hitbox, Mapping):
            issues.append(MapContentIssue("error", "interactive_object_missing_hitbox", f"{path}.hitbox"))
        _polygon(hitbox, f"{path}.hitbox", issues, required=item.get("kind") != "decorative")
        _polygon(item.get("footprint"), f"{path}.footprint", issues, required=False)
        sprite = _mapping(item.get("sprite"))
        if sprite:
            asset_id = _required_text(sprite, "asset_id", f"{path}.sprite.asset_id", issues)
            _positive_integer(sprite, "width", f"{path}.sprite.width", issues)
            _positive_integer(sprite, "height", f"{path}.sprite.height", issues)
            _asset_allowed(asset_id, allowed_asset_ids, f"{path}.sprite.asset_id", issues)
        child_map_id = _text(item.get("child_map_id"))
        if child_map_id and known_map_ids and child_map_id not in known_map_ids:
            issues.append(MapContentIssue("error", "unknown_child_map_id", f"{path}.child_map_id", child_map_id))

    routes = _rows(raw.get("route_geometry"))
    route_ids = _unique_ids(routes, "definition.route_geometry", issues, key="route_id")
    for index, route in enumerate(routes):
        path = f"definition.route_geometry[{index}]"
        points = _rows_or_points(route.get("points"))
        if len(points) < 2:
            issues.append(MapContentIssue("error", "route_geometry_too_short", f"{path}.points"))
        for point_index, point in enumerate(points):
            _point(point, f"{path}.points[{point_index}]", issues)
        route_id = _text(route.get("route_id"))
        if route_id and canonical_route_ids and route_id not in canonical_route_ids:
            issues.append(MapContentIssue("error", "unknown_canonical_route_id", f"{path}.route_id", route_id))

    labels = _rows(raw.get("labels"))
    _unique_ids(labels, "definition.labels", issues)
    background = _mapping(raw.get("background"))
    if background:
        asset_id = _required_text(background, "asset_id", "definition.background.asset_id", issues)
        _asset_allowed(asset_id, allowed_asset_ids, "definition.background.asset_id", issues)
    else:
        issues.append(MapContentIssue("warning", "background_missing", "definition.background"))
    if not objects:
        issues.append(MapContentIssue("warning", "map_has_no_objects", "definition.objects"))
    if not labels:
        issues.append(MapContentIssue("warning", "map_has_no_labels", "definition.labels"))

    parent_map_id = _text(raw.get("parent_map_id"))
    if parent_map_id and known_map_ids and parent_map_id not in known_map_ids:
        issues.append(MapContentIssue("error", "unknown_parent_map_id", "definition.parent_map_id", parent_map_id))
    if len(object_ids) != len(objects) or len(route_ids) != len(routes):
        pass
    return _report(raw, issues)


def _report(raw: Mapping[str, Any], issues: list[MapContentIssue]) -> MapContentReport:
    try:
        canonical = canonical_map_json(raw)
        revision = map_content_revision(raw)
    except (TypeError, ValueError, MapContractError):
        canonical = "{}"
        revision = ""
    return MapContentReport(
        ok=not any(issue.severity == "error" for issue in issues),
        revision=revision,
        canonical_json=canonical,
        issues=tuple(issues),
    )


def _unique_ids(rows: tuple[Mapping[str, Any], ...], path: str, issues: list[MapContentIssue], *, key: str = "id") -> set[str]:
    seen: set[str] = set()
    for index, row in enumerate(rows):
        value = _required_text(row, key, f"{path}[{index}].{key}", issues)
        if value in seen:
            issues.append(MapContentIssue("error", f"duplicate_{key}", f"{path}[{index}].{key}", value))
        seen.add(value)
    return seen


def _polygon(value: object, path: str, issues: list[MapContentIssue], *, required: bool) -> None:
    raw = _mapping(value)
    if not raw:
        if required:
            return
        return
    points = _rows_or_points(raw.get("points"))
    if len(points) < 3:
        issues.append(MapContentIssue("error", "degenerate_polygon", f"{path}.points"))
    for index, point in enumerate(points):
        _point(point, f"{path}.points[{index}]", issues)


def _point(value: object, path: str, issues: list[MapContentIssue]) -> None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        issues.append(MapContentIssue("error", "invalid_point", path))
        return
    if any(not isinstance(item, int) or isinstance(item, bool) for item in value):
        issues.append(MapContentIssue("error", "point_requires_integer_coordinates", path))


def _asset_allowed(value: str, allowed: Sequence[str], path: str, issues: list[MapContentIssue]) -> None:
    if value and allowed and value not in set(allowed):
        issues.append(MapContentIssue("error", "asset_id_not_allowed", path, value))


def _required_text(raw: Mapping[str, Any], key: str, path: str, issues: list[MapContentIssue]) -> str:
    value = _text(raw.get(key))
    if not value:
        issues.append(MapContentIssue("error", "required", path))
    return value


def _positive_integer(raw: Mapping[str, Any], key: str, path: str, issues: list[MapContentIssue]) -> None:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        issues.append(MapContentIssue("error", "positive_integer_required", path))


def _integer(raw: Mapping[str, Any], key: str, path: str, issues: list[MapContentIssue]) -> None:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        issues.append(MapContentIssue("error", "integer_required", path))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _rows_or_points(value: object) -> tuple[object, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(value)


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
