"""Shared rectangular actor-footprint helpers for deterministic map runtime."""
from __future__ import annotations

from collections.abc import Iterable

from app.apps.rpg.world.map_effective_geometry import effective_is_walkable, effective_movement_cost
from app.apps.rpg.world.map_grid_contracts import GridActorPlacement, GridMapDefinition, GridPoint
from typing import Any


def actor_footprint_cells(
    actor: GridActorPlacement,
    *,
    anchor: GridPoint | None = None,
) -> tuple[GridPoint, ...]:
    """Return row-major cells for an actor whose anchor is the top-left cell."""

    column, row = anchor if anchor is not None else actor.cell
    return tuple(
        (column + dx, row + dy)
        for dy in range(actor.footprint_height)
        for dx in range(actor.footprint_width)
    )


def occupied_actor_cells(
    actors: Iterable[GridActorPlacement],
    *,
    exclude_actor_id: str | None = None,
) -> dict[GridPoint, str]:
    occupied: dict[GridPoint, str] = {}
    for actor in actors:
        if actor.actor_id == exclude_actor_id:
            continue
        for cell in actor_footprint_cells(actor):
            previous = occupied.get(cell)
            if previous is not None and previous != actor.actor_id:
                raise ValueError(
                    f"overlapping_actor_footprints:{previous}:{actor.actor_id}:"
                    f"{cell[0]}:{cell[1]}"
                )
            occupied[cell] = actor.actor_id
    return occupied


def footprint_is_inside(
    definition: GridMapDefinition,
    actor: GridActorPlacement,
    *,
    anchor: GridPoint | None = None,
) -> bool:
    return all(_inside(definition, cell) for cell in actor_footprint_cells(actor, anchor=anchor))


def footprint_is_walkable(
    definition: GridMapDefinition,
    snapshot: Any,
    actor: GridActorPlacement,
    *,
    anchor: GridPoint | None = None,
) -> bool:
    cells = actor_footprint_cells(actor, anchor=anchor)
    return all(
        _inside(definition, cell) and effective_is_walkable(definition, snapshot, cell)
        for cell in cells
    )


def footprint_overlaps(
    actor: GridActorPlacement,
    occupied: set[GridPoint] | dict[GridPoint, str],
    *,
    anchor: GridPoint | None = None,
) -> bool:
    occupied_cells = set(occupied)
    return any(cell in occupied_cells for cell in actor_footprint_cells(actor, anchor=anchor))


def footprint_movement_cost(
    definition: GridMapDefinition,
    snapshot: Any,
    actor: GridActorPlacement,
    *,
    anchor: GridPoint,
) -> int:
    """Use the highest terrain cost under the footprint for one anchor step."""

    return max(
        effective_movement_cost(definition, snapshot, cell)
        for cell in actor_footprint_cells(actor, anchor=anchor)
    )


def _inside(definition: GridMapDefinition, cell: GridPoint) -> bool:
    column, row = cell
    return 0 <= column < definition.width and 0 <= row < definition.height
