"""Authoritative event-driven runtime for campaign grid map instances."""
from __future__ import annotations

import heapq
from typing import Any, Final, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.apps.rpg.world.map_actor_footprints import (
    actor_footprint_cells,
    footprint_is_inside,
    footprint_is_walkable,
    footprint_movement_cost,
    footprint_overlaps,
    occupied_actor_cells,
)
from app.apps.rpg.world.map_effective_geometry import effective_terrain_rows, normalize_terrain_overrides
from app.apps.rpg.world.map_grid_contracts import GridActorPlacement, GridMapDefinition, GridPoint

MAP_INSTANCE_SCHEMA_VERSION: Final = 1
PATHFINDER_VERSION = 2
MAP_REDUCER_VERSION = 1


class FrozenRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CampaignMapInstanceSnapshot(FrozenRuntimeModel):
    schema_version: Literal[1] = MAP_INSTANCE_SCHEMA_VERSION
    map_instance_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    map_id: str = Field(min_length=1)
    definition_revision: int = Field(ge=1)
    definition_hash: str = Field(pattern=r"^sha256:")
    map_state_revision: int = Field(default=0, ge=0)
    applied_event_sequence: int = Field(default=0, ge=0)
    reducer_version: int = MAP_REDUCER_VERSION
    actors: tuple[GridActorPlacement, ...] = ()
    object_states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    route_states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    hazard_states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    terrain_overrides: dict[str, str] = Field(default_factory=dict)
    geometry_patch_ids: tuple[str, ...] = ()
    revealed_secret_ids: tuple[str, ...] = ()
    revealed_actor_ids: tuple[str, ...] = ()
    applied_command_ids: tuple[str, ...] = ()
    initialization_operation_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_snapshot(self) -> "CampaignMapInstanceSnapshot":
        actor_ids = [actor.actor_id for actor in self.actors]
        if len(actor_ids) != len(set(actor_ids)):
            raise ValueError("duplicate_map_instance_actor")
        anchors = [actor.cell for actor in self.actors]
        if len(anchors) != len(set(anchors)):
            raise ValueError("duplicate_map_instance_actor_cell")
        footprint_cells = [
            cell
            for actor in self.actors
            for cell in actor_footprint_cells(actor)
        ]
        if len(footprint_cells) != len(set(footprint_cells)):
            raise ValueError("overlapping_map_instance_actor_footprints")
        if len(self.geometry_patch_ids) != len(set(self.geometry_patch_ids)):
            raise ValueError("duplicate_map_geometry_patch_id")
        normalized = normalize_terrain_overrides(self.terrain_overrides)
        if normalized != self.terrain_overrides:
            raise ValueError("noncanonical_map_terrain_overrides")
        return self

    def actor(self, actor_id: str) -> GridActorPlacement:
        try:
            return next(actor for actor in self.actors if actor.actor_id == actor_id)
        except StopIteration as exc:
            raise MapMovementError("actor_not_on_map", actor_id) from exc


class MoveActorCommand(FrozenRuntimeModel):
    command_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    destination: GridPoint
    expected_map_state_revision: int = Field(ge=0)


class ActorMovedEvent(FrozenRuntimeModel):
    event_id: str = Field(min_length=1)
    event_type: Literal["actor_moved"] = "actor_moved"
    command_id: str = Field(min_length=1)
    map_instance_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    from_cell: GridPoint
    to_cell: GridPoint
    path: tuple[GridPoint, ...]
    movement_cost: int = Field(ge=0)
    map_state_revision_before: int = Field(ge=0)
    map_state_revision_after: int = Field(ge=1)
    event_sequence: int = Field(ge=1)
    pathfinder_version: int = PATHFINDER_VERSION
    reducer_version: int = MAP_REDUCER_VERSION


class MapMovementError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


def create_map_instance_snapshot(
    *,
    map_instance_id: str,
    campaign_id: str,
    location_id: str,
    definition: GridMapDefinition,
    actors: Iterable[GridActorPlacement] = (),
) -> CampaignMapInstanceSnapshot:
    placements = tuple(actors)
    occupied: set[GridPoint] = set()
    for actor in placements:
        cells = actor_footprint_cells(actor)
        if not footprint_is_inside(definition, actor):
            raise MapMovementError("actor_spawn_footprint_out_of_bounds", actor.actor_id)
        if any(not definition.is_walkable(cell) for cell in cells):
            raise MapMovementError("actor_spawn_not_walkable", actor.actor_id)
        if any(cell in occupied for cell in cells):
            raise MapMovementError("actor_spawn_footprint_occupied", actor.actor_id)
        occupied.update(cells)
    return CampaignMapInstanceSnapshot(
        map_instance_id=map_instance_id,
        campaign_id=campaign_id,
        location_id=location_id,
        map_id=definition.map_id,
        definition_revision=definition.definition_revision,
        definition_hash=definition.definition_hash,
        actors=placements,
    )


def resolve_move_command(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    command: MoveActorCommand,
) -> tuple[ActorMovedEvent, CampaignMapInstanceSnapshot]:
    _validate_definition_binding(definition, snapshot)
    if command.expected_map_state_revision != snapshot.map_state_revision:
        raise MapMovementError(
            "stale_map_state_revision",
            f"expected={command.expected_map_state_revision}:current={snapshot.map_state_revision}",
        )
    if command.command_id in snapshot.applied_command_ids:
        raise MapMovementError("command_already_applied", command.command_id)
    actor = snapshot.actor(command.actor_id)
    occupied = set(
        occupied_actor_cells(
            snapshot.actors,
            exclude_actor_id=command.actor_id,
        )
    )
    if not footprint_is_inside(definition, actor, anchor=command.destination):
        raise MapMovementError("destination_footprint_out_of_bounds", command.actor_id)
    if footprint_overlaps(actor, occupied, anchor=command.destination):
        raise MapMovementError("destination_occupied", command.actor_id)
    if not footprint_is_walkable(
        definition,
        snapshot,
        actor,
        anchor=command.destination,
    ):
        raise MapMovementError("destination_blocked", command.actor_id)
    path, movement_cost = _find_path(
        definition,
        snapshot,
        actor,
        command.destination,
        occupied,
    )
    if not path:
        raise MapMovementError("destination_unreachable", command.actor_id)
    event_sequence = snapshot.applied_event_sequence + 1
    after_revision = snapshot.map_state_revision + 1
    event = ActorMovedEvent(
        event_id=f"map-event:{snapshot.map_instance_id}:{event_sequence}",
        command_id=command.command_id,
        map_instance_id=snapshot.map_instance_id,
        actor_id=command.actor_id,
        from_cell=actor.cell,
        to_cell=command.destination,
        path=path,
        movement_cost=movement_cost,
        map_state_revision_before=snapshot.map_state_revision,
        map_state_revision_after=after_revision,
        event_sequence=event_sequence,
    )
    return event, reduce_map_event(snapshot, event)


def reduce_map_event(
    snapshot: CampaignMapInstanceSnapshot,
    event: ActorMovedEvent,
) -> CampaignMapInstanceSnapshot:
    if event.map_instance_id != snapshot.map_instance_id:
        raise MapMovementError("event_map_instance_mismatch")
    if event.map_state_revision_before != snapshot.map_state_revision:
        raise MapMovementError("event_revision_mismatch")
    actor = snapshot.actor(event.actor_id)
    if actor.cell != event.from_cell:
        raise MapMovementError("event_actor_origin_mismatch", event.actor_id)
    if event.event_sequence != snapshot.applied_event_sequence + 1:
        raise MapMovementError("event_sequence_mismatch")
    actors = tuple(
        placement.model_copy(update={"cell": event.to_cell})
        if placement.actor_id == event.actor_id
        else placement
        for placement in snapshot.actors
    )
    return snapshot.model_copy(
        update={
            "actors": actors,
            "map_state_revision": event.map_state_revision_after,
            "applied_event_sequence": event.event_sequence,
            "applied_command_ids": tuple(
                dict.fromkeys((*snapshot.applied_command_ids, event.command_id))
            ),
        }
    )


def replay_map_events(
    initial: CampaignMapInstanceSnapshot,
    events: Iterable[ActorMovedEvent],
) -> CampaignMapInstanceSnapshot:
    snapshot = initial
    for event in sorted(events, key=lambda row: row.event_sequence):
        snapshot = reduce_map_event(snapshot, event)
    return snapshot


def project_observer_map(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    *,
    observer_actor_id: str,
) -> dict[str, Any]:
    _validate_definition_binding(definition, snapshot)
    revealed_secrets = set(snapshot.revealed_secret_ids)
    revealed_actors = set(snapshot.revealed_actor_ids)
    visible_actors = [
        actor.model_dump(mode="json")
        for actor in snapshot.actors
        if not actor.hidden
        or actor.actor_id == observer_actor_id
        or actor.actor_id in revealed_actors
    ]
    portals = [
        portal.model_dump(mode="json")
        for portal in definition.portals
        if not portal.secret or portal.portal_id in revealed_secrets
    ]
    spawn_points = [
        spawn.model_dump(mode="json")
        for spawn in definition.spawn_points
        if not spawn.secret or spawn.spawn_point_id in revealed_secrets
    ]
    zones = [
        zone.model_dump(mode="json")
        for zone in definition.zones
        if not zone.secret or zone.zone_id in revealed_secrets
    ]
    return {
        "map_instance_id": snapshot.map_instance_id,
        "map_id": definition.map_id,
        "definition_revision": definition.definition_revision,
        "definition_hash": definition.definition_hash,
        "map_state_revision": snapshot.map_state_revision,
        "grid": {
            "width": definition.width,
            "height": definition.height,
            "transform": definition.transform.model_dump(mode="json"),
            "terrain_rows": effective_terrain_rows(definition, snapshot),
            "terrain_palette": [
                rule.model_dump(mode="json") for rule in definition.terrain_palette
            ],
        },
        "portals": portals,
        "spawn_points": spawn_points,
        "zones": zones,
        "actors": visible_actors,
        "object_states": dict(snapshot.object_states),
        "route_states": dict(snapshot.route_states),
    }


def _validate_definition_binding(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
) -> None:
    if snapshot.map_id != definition.map_id:
        raise MapMovementError("map_definition_id_mismatch")
    if snapshot.definition_revision != definition.definition_revision:
        raise MapMovementError("map_definition_revision_mismatch")
    if snapshot.definition_hash != definition.definition_hash:
        raise MapMovementError("map_definition_hash_mismatch")


def _find_path(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    actor: GridActorPlacement,
    destination: GridPoint,
    occupied: set[GridPoint],
) -> tuple[tuple[GridPoint, ...], int]:
    start = actor.cell
    if start == destination:
        return (start,), 0
    frontier: list[tuple[int, int, int, GridPoint]] = []
    heapq.heappush(frontier, (0, 0, 0, start))
    came_from: dict[GridPoint, GridPoint | None] = {start: None}
    cost_so_far: dict[GridPoint, int] = {start: 0}
    insertion = 0
    while frontier:
        _, current_cost, _, current = heapq.heappop(frontier)
        if current == destination:
            break
        if current_cost != cost_so_far[current]:
            continue
        for neighbor, step_cost in _neighbors(
            definition,
            snapshot,
            actor,
            current,
            occupied,
        ):
            new_cost = current_cost + step_cost
            old_cost = cost_so_far.get(neighbor)
            if old_cost is not None and new_cost >= old_cost:
                continue
            cost_so_far[neighbor] = new_cost
            came_from[neighbor] = current
            insertion += 1
            heuristic = _octile_distance(neighbor, destination)
            heapq.heappush(
                frontier,
                (new_cost + heuristic, new_cost, insertion, neighbor),
            )
    if destination not in came_from:
        return (), 0
    path: list[GridPoint] = []
    step: GridPoint | None = destination
    while step is not None:
        path.append(step)
        step = came_from[step]
    path.reverse()
    return tuple(path), cost_so_far[destination]


def _neighbors(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    actor: GridActorPlacement,
    cell: GridPoint,
    occupied: set[GridPoint],
) -> tuple[tuple[GridPoint, int], ...]:
    column, row = cell
    offsets = (
        (-1, -1),
        (0, -1),
        (1, -1),
        (-1, 0),
        (1, 0),
        (-1, 1),
        (0, 1),
        (1, 1),
    )
    rows: list[tuple[GridPoint, int]] = []
    for dx, dy in offsets:
        target = (column + dx, row + dy)
        if not _anchor_is_open(definition, snapshot, actor, target, occupied):
            continue
        diagonal = dx != 0 and dy != 0
        if diagonal:
            side_a = (column + dx, row)
            side_b = (column, row + dy)
            if not _anchor_is_open(
                definition,
                snapshot,
                actor,
                side_a,
                occupied,
            ) or not _anchor_is_open(
                definition,
                snapshot,
                actor,
                side_b,
                occupied,
            ):
                continue
        terrain_cost = footprint_movement_cost(
            definition,
            snapshot,
            actor,
            anchor=target,
        )
        step_cost = (terrain_cost * 14 + 9) // 10 if diagonal else terrain_cost
        rows.append((target, step_cost))
    return tuple(rows)


def _anchor_is_open(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    actor: GridActorPlacement,
    anchor: GridPoint,
    occupied: set[GridPoint],
) -> bool:
    return (
        footprint_is_inside(definition, actor, anchor=anchor)
        and not footprint_overlaps(actor, occupied, anchor=anchor)
        and footprint_is_walkable(definition, snapshot, actor, anchor=anchor)
    )


def _octile_distance(left: GridPoint, right: GridPoint) -> int:
    dx = abs(left[0] - right[0])
    dy = abs(left[1] - right[1])
    diagonal = min(dx, dy)
    straight = max(dx, dy) - diagonal
    return diagonal * 14 + straight * 10
