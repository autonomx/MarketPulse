"""Stateless deterministic edit operations for RPG map definition content."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass

from app.apps.rpg.world.map_content_validation import MapContentReport, validate_map_content
from typing import Any

_ALLOWED_OPERATIONS = {
    "move_object",
    "assign_object_asset",
    "set_object_polygon",
    "set_child_map",
    "upsert_route",
    "remove_route",
    "set_background_asset",
}


class MapContentEditError(ValueError):
    def __init__(self, code: str, path: str = "operations", detail: str = "") -> None:
        self.code = code
        self.path = path
        self.detail = detail
        message = f"{path}:{code}"
        if detail:
            message = f"{message}:{detail}"
        super().__init__(message)


@dataclass(frozen=True)
class MapContentEditResult:
    definition: Mapping[str, Any]
    report: MapContentReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "definition": deepcopy(dict(self.definition)),
            "report": asdict(self.report),
        }


def apply_map_content_operations(
    definition: object,
    operations: object,
    *,
    canonical_route_ids: Sequence[str] = (),
    known_map_ids: Sequence[str] = (),
    allowed_asset_ids: Sequence[str] = (),
) -> MapContentEditResult:
    if not isinstance(definition, Mapping):
        raise MapContentEditError("definition_required", "definition")
    draft = deepcopy(dict(definition))
    if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes)):
        raise MapContentEditError("operations_must_be_array")
    for index, value in enumerate(operations):
        path = f"operations[{index}]"
        if not isinstance(value, Mapping):
            raise MapContentEditError("operation_must_be_object", path)
        _apply(draft, value, path)
    report = validate_map_content(
        draft,
        canonical_route_ids=canonical_route_ids,
        known_map_ids=known_map_ids,
        allowed_asset_ids=allowed_asset_ids,
    )
    return MapContentEditResult(definition=draft, report=report)


def _apply(draft: dict[str, Any], operation: Mapping[str, Any], path: str) -> None:
    kind = _required_text(operation.get("type"), f"{path}.type")
    if kind not in _ALLOWED_OPERATIONS:
        raise MapContentEditError("unsupported_operation", f"{path}.type", kind)
    if kind == "move_object":
        item = _object(draft, operation, path)
        item["x"] = _integer(operation.get("x"), f"{path}.x")
        item["y"] = _integer(operation.get("y"), f"{path}.y")
        render = raw_render_order if isinstance(raw_render_order := item.get("render_order"), dict) else {}
        render["sort_y"] = item["y"]
        render.setdefault("offset", 0)
        item["render_order"] = render
        return
    if kind == "assign_object_asset":
        item = _object(draft, operation, path)
        item["sprite"] = {
            "asset_id": _required_text(operation.get("asset_id"), f"{path}.asset_id"),
            "width": _positive_integer(operation.get("width"), f"{path}.width"),
            "height": _positive_integer(operation.get("height"), f"{path}.height"),
        }
        return
    if kind == "set_object_polygon":
        item = _object(draft, operation, path)
        field = _required_text(operation.get("field"), f"{path}.field")
        if field not in {"footprint", "hitbox"}:
            raise MapContentEditError("unsupported_polygon_field", f"{path}.field", field)
        item[field] = {
            "kind": "polygon",
            "points": _points(operation.get("points"), f"{path}.points"),
        }
        return
    if kind == "set_child_map":
        item = _object(draft, operation, path)
        child_map_id = _text(operation.get("child_map_id"))
        item["child_map_id"] = child_map_id or None
        return
    if kind == "upsert_route":
        route_id = _required_text(operation.get("route_id"), f"{path}.route_id")
        route = {
            "route_id": route_id,
            "points": _points(operation.get("points"), f"{path}.points"),
            "style": _text(operation.get("style")) or "road",
        }
        routes = _mutable_rows(draft, "route_geometry")
        routes = [item for item in routes if _text(item.get("route_id")) != route_id]
        routes.append(route)
        draft["route_geometry"] = sorted(routes, key=lambda item: _text(item.get("route_id")))
        return
    if kind == "remove_route":
        route_id = _required_text(operation.get("route_id"), f"{path}.route_id")
        routes = _mutable_rows(draft, "route_geometry")
        if not any(_text(item.get("route_id")) == route_id for item in routes):
            raise MapContentEditError("route_not_found", f"{path}.route_id", route_id)
        draft["route_geometry"] = [item for item in routes if _text(item.get("route_id")) != route_id]
        return
    if kind == "set_background_asset":
        bounds = raw_bounds if isinstance(raw_bounds := draft.get("bounds"), Mapping) else {}
        draft["background"] = {
            "asset_id": _required_text(operation.get("asset_id"), f"{path}.asset_id"),
            "destination_bounds": deepcopy(dict(bounds)),
            "source_crop": operation.get("source_crop"),
        }


def _object(draft: dict[str, Any], operation: Mapping[str, Any], path: str) -> dict[str, Any]:
    object_id = _required_text(operation.get("object_id"), f"{path}.object_id")
    objects = _mutable_rows(draft, "objects")
    for item in objects:
        if _text(item.get("id")) == object_id:
            return item
    raise MapContentEditError("object_not_found", f"{path}.object_id", object_id)


def _mutable_rows(draft: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = draft.get(key)
    if value is None:
        rows: list[dict[str, Any]] = []
        draft[key] = rows
        return rows
    if not isinstance(value, list):
        raise MapContentEditError("definition_field_must_be_array", f"definition.{key}")
    rows = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise MapContentEditError("definition_item_must_be_object", f"definition.{key}[{index}]")
        rows.append(item)
    return rows


def _points(value: object, path: str) -> list[list[int]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MapContentEditError("points_must_be_array", path)
    points = []
    for index, point in enumerate(value):
        point_path = f"{path}[{index}]"
        if not isinstance(point, Sequence) or isinstance(point, (str, bytes)) or len(point) != 2:
            raise MapContentEditError("invalid_point", point_path)
        points.append([
            _integer(point[0], f"{point_path}[0]"),
            _integer(point[1], f"{point_path}[1]"),
        ])
    return points


def _required_text(value: object, path: str) -> str:
    text = _text(value)
    if not text:
        raise MapContentEditError("required", path)
    return text


def _integer(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise MapContentEditError("integer_required", path)
    return value


def _positive_integer(value: object, path: str) -> int:
    integer = _integer(value, path)
    if integer <= 0:
        raise MapContentEditError("positive_integer_required", path)
    return integer


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
