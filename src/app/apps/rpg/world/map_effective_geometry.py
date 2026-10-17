"""Effective terrain helpers for immutable definitions plus campaign overlays."""
from __future__ import annotations

from typing import Any, Protocol

from app.apps.rpg.world.map_grid_contracts import GridMapDefinition, GridPoint, TerrainRule


class GeometrySnapshot(Protocol):
    terrain_overrides: dict[str, str]


def geometry_cell_key(cell: GridPoint) -> str:
    return f"{cell[0]},{cell[1]}"


def parse_geometry_cell_key(value: str) -> GridPoint:
    try:
        column, row = value.split(",", 1)
        return int(column), int(row)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_geometry_cell_key:{value}") from exc


def definition_terrain_code(
    definition: GridMapDefinition,
    cell: GridPoint,
) -> str:
    definition.require_inside(cell)
    column, row = cell
    return definition.terrain_rows[row][column]


def terrain_rule_for_code(
    definition: GridMapDefinition,
    code: str,
) -> TerrainRule:
    try:
        return next(rule for rule in definition.terrain_palette if rule.code == code)
    except StopIteration as exc:
        raise ValueError(f"unknown_effective_terrain_code:{code}") from exc


def effective_terrain_code(
    definition: GridMapDefinition,
    snapshot: GeometrySnapshot,
    cell: GridPoint,
) -> str:
    definition.require_inside(cell)
    return snapshot.terrain_overrides.get(
        geometry_cell_key(cell),
        definition_terrain_code(definition, cell),
    )


def effective_terrain_rule(
    definition: GridMapDefinition,
    snapshot: GeometrySnapshot,
    cell: GridPoint,
) -> TerrainRule:
    code = effective_terrain_code(definition, snapshot, cell)
    return terrain_rule_for_code(definition, code)


def effective_is_walkable(
    definition: GridMapDefinition,
    snapshot: GeometrySnapshot,
    cell: GridPoint,
) -> bool:
    return effective_terrain_rule(definition, snapshot, cell).walkable


def effective_movement_cost(
    definition: GridMapDefinition,
    snapshot: GeometrySnapshot,
    cell: GridPoint,
) -> int:
    return effective_terrain_rule(definition, snapshot, cell).movement_cost


def effective_blocks_sight(
    definition: GridMapDefinition,
    snapshot: GeometrySnapshot,
    cell: GridPoint,
) -> bool:
    return effective_terrain_rule(definition, snapshot, cell).blocks_sight


def effective_terrain_rows(
    definition: GridMapDefinition,
    snapshot: GeometrySnapshot,
    *,
    unknown_cells: set[GridPoint] | None = None,
    unknown_code: str = "?",
) -> list[str]:
    rows: list[str] = []
    for row in range(definition.height):
        values: list[str] = []
        for column in range(definition.width):
            cell = (column, row)
            if unknown_cells is not None and cell not in unknown_cells:
                values.append(unknown_code)
            else:
                values.append(effective_terrain_code(definition, snapshot, cell))
        rows.append("".join(values))
    return rows


def normalize_terrain_overrides(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, code in value.items():
        cell = parse_geometry_cell_key(str(key))
        normalized[geometry_cell_key(cell)] = str(code)
    return normalized
