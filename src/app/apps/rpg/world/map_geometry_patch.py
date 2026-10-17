"""Event-sourced campaign geometry overlays for immutable map definitions."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.apps.rpg.world.map_actor_footprints import occupied_actor_cells
from app.apps.rpg.world.map_effective_geometry import (
    definition_terrain_code,
    geometry_cell_key,
    terrain_rule_for_code,
)
from app.apps.rpg.world.map_grid_contracts import GridMapDefinition, GridPoint
from app.apps.rpg.world.map_instance_runtime import (
    ActorMovedEvent,
    CampaignMapInstanceSnapshot,
    FrozenRuntimeModel,
    MapMovementError,
    reduce_map_event,
)


class GeometryCellPatch(FrozenRuntimeModel):
    cell: GridPoint
    terrain_code: str | None = Field(default=None, min_length=1, max_length=1)


class ApplyGeometryPatchCommand(FrozenRuntimeModel):
    command_id: str = Field(min_length=1)
    patch_id: str = Field(min_length=1)
    expected_map_state_revision: int = Field(ge=0)
    cells: tuple[GeometryCellPatch, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cells(self) -> "ApplyGeometryPatchCommand":
        cells = [row.cell for row in self.cells]
        if len(cells) != len(set(cells)):
            raise ValueError("duplicate_geometry_patch_cell")
        return self


class MapGeometryPatchedEvent(FrozenRuntimeModel):
    event_id: str = Field(min_length=1)
    event_type: Literal["map_geometry_patched"] = "map_geometry_patched"
    command_id: str = Field(min_length=1)
    patch_id: str = Field(min_length=1)
    map_instance_id: str = Field(min_length=1)
    cells: tuple[GeometryCellPatch, ...]
    map_state_revision_before: int = Field(ge=0)
    map_state_revision_after: int = Field(ge=1)
    event_sequence: int = Field(ge=1)


CampaignMapResolvedEvent = ActorMovedEvent | MapGeometryPatchedEvent


def resolve_geometry_patch_command(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    command: ApplyGeometryPatchCommand,
) -> tuple[MapGeometryPatchedEvent, CampaignMapInstanceSnapshot]:
    if snapshot.map_id != definition.map_id:
        raise MapMovementError("map_definition_id_mismatch")
    if snapshot.definition_revision != definition.definition_revision:
        raise MapMovementError("map_definition_revision_mismatch")
    if snapshot.definition_hash != definition.definition_hash:
        raise MapMovementError("map_definition_hash_mismatch")
    if command.expected_map_state_revision != snapshot.map_state_revision:
        raise MapMovementError(
            "stale_map_state_revision",
            f"expected={command.expected_map_state_revision}:current={snapshot.map_state_revision}",
        )
    if command.command_id in snapshot.applied_command_ids:
        raise MapMovementError("command_already_applied", command.command_id)
    if command.patch_id in snapshot.geometry_patch_ids:
        raise MapMovementError("geometry_patch_already_applied", command.patch_id)
    occupied = occupied_actor_cells(snapshot.actors)
    for patch in command.cells:
        definition.require_inside(patch.cell)
        code = (
            patch.terrain_code
            if patch.terrain_code is not None
            else definition_terrain_code(definition, patch.cell)
        )
        try:
            rule = terrain_rule_for_code(definition, code)
        except ValueError as exc:
            raise MapMovementError("geometry_patch_terrain_unknown", code) from exc
        if not rule.walkable and patch.cell in occupied:
            raise MapMovementError(
                "geometry_patch_actor_occupied",
                occupied[patch.cell],
            )
    sequence = snapshot.applied_event_sequence + 1
    event = MapGeometryPatchedEvent(
        event_id=f"map-event:{snapshot.map_instance_id}:{sequence}",
        command_id=command.command_id,
        patch_id=command.patch_id,
        map_instance_id=snapshot.map_instance_id,
        cells=command.cells,
        map_state_revision_before=snapshot.map_state_revision,
        map_state_revision_after=snapshot.map_state_revision + 1,
        event_sequence=sequence,
    )
    return event, reduce_campaign_map_event(snapshot, event)


def reduce_campaign_map_event(
    snapshot: CampaignMapInstanceSnapshot,
    event: CampaignMapResolvedEvent,
) -> CampaignMapInstanceSnapshot:
    if isinstance(event, ActorMovedEvent):
        return reduce_map_event(snapshot, event)
    if event.map_instance_id != snapshot.map_instance_id:
        raise MapMovementError("event_map_instance_mismatch")
    if event.map_state_revision_before != snapshot.map_state_revision:
        raise MapMovementError("event_revision_mismatch")
    if event.event_sequence != snapshot.applied_event_sequence + 1:
        raise MapMovementError("event_sequence_mismatch")
    overrides = dict(snapshot.terrain_overrides)
    for patch in event.cells:
        key = geometry_cell_key(patch.cell)
        if patch.terrain_code is None:
            overrides.pop(key, None)
        else:
            overrides[key] = patch.terrain_code
    return snapshot.model_copy(
        update={
            "terrain_overrides": overrides,
            "geometry_patch_ids": tuple(
                dict.fromkeys((*snapshot.geometry_patch_ids, event.patch_id))
            ),
            "map_state_revision": event.map_state_revision_after,
            "applied_event_sequence": event.event_sequence,
            "applied_command_ids": tuple(
                dict.fromkeys((*snapshot.applied_command_ids, event.command_id))
            ),
        }
    )


