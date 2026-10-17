"""Resolved cross-map portal events for living NPC spatial goals."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.apps.rpg.world.map_actor_footprints import (
    footprint_is_inside,
    footprint_is_walkable,
    footprint_overlaps,
    occupied_actor_cells,
)
from app.apps.rpg.world.map_grid_contracts import GridActorPlacement, GridMapDefinition, GridPoint
from app.apps.rpg.world.map_instance_runtime import (
    ActorMovedEvent,
    CampaignMapInstanceSnapshot,
    FrozenRuntimeModel,
    MapMovementError,
    reduce_map_event,
)


class ActorExitedMapEvent(FrozenRuntimeModel):
    event_id: str = Field(min_length=1)
    event_type: Literal["actor_exited_map"] = "actor_exited_map"
    command_id: str = Field(min_length=1)
    transition_id: str = Field(min_length=1)
    map_instance_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    from_cell: GridPoint
    portal_id: str = Field(min_length=1)
    target_map_instance_id: str = Field(min_length=1)
    target_cell: GridPoint
    map_state_revision_before: int = Field(ge=0)
    map_state_revision_after: int = Field(ge=1)
    event_sequence: int = Field(ge=1)


class ActorEnteredMapEvent(FrozenRuntimeModel):
    event_id: str = Field(min_length=1)
    event_type: Literal["actor_entered_map"] = "actor_entered_map"
    command_id: str = Field(min_length=1)
    transition_id: str = Field(min_length=1)
    map_instance_id: str = Field(min_length=1)
    actor: GridActorPlacement
    portal_id: str = Field(min_length=1)
    source_map_instance_id: str = Field(min_length=1)
    source_cell: GridPoint
    map_state_revision_before: int = Field(ge=0)
    map_state_revision_after: int = Field(ge=1)
    event_sequence: int = Field(ge=1)


NpcSpatialResolvedMapEvent = ActorMovedEvent | ActorExitedMapEvent | ActorEnteredMapEvent


def resolve_portal_transition(
    source_definition: GridMapDefinition,
    source_snapshot: CampaignMapInstanceSnapshot,
    target_definition: GridMapDefinition,
    target_snapshot: CampaignMapInstanceSnapshot,
    *,
    actor_id: str,
    portal_id: str,
    transition_id: str,
) -> tuple[
    ActorExitedMapEvent,
    ActorEnteredMapEvent,
    CampaignMapInstanceSnapshot,
    CampaignMapInstanceSnapshot,
]:
    """Resolve one atomic two-map transfer after the actor reaches the portal."""

    if source_snapshot.campaign_id != target_snapshot.campaign_id:
        raise MapMovementError("portal_campaign_mismatch")
    if source_snapshot.map_id != source_definition.map_id:
        raise MapMovementError("portal_source_definition_mismatch")
    if target_snapshot.map_id != target_definition.map_id:
        raise MapMovementError("portal_target_definition_mismatch")
    portal = next(
        (row for row in source_definition.portals if row.portal_id == portal_id),
        None,
    )
    if portal is None:
        raise MapMovementError("portal_not_found", portal_id)
    if portal.source.map_id != source_definition.map_id:
        raise MapMovementError("portal_direction_invalid", portal_id)
    if portal.target.map_id != target_definition.map_id:
        raise MapMovementError("portal_target_map_mismatch", portal_id)
    if portal.state != "open":
        raise MapMovementError("portal_not_open", portal_id)
    actor = source_snapshot.actor(actor_id)
    if actor.cell != portal.source.cell:
        raise MapMovementError("actor_not_at_portal", actor_id)
    target_actor = actor.model_copy(update={"cell": portal.target.cell})
    if not footprint_is_inside(target_definition, target_actor):
        raise MapMovementError("portal_target_footprint_out_of_bounds", portal_id)
    if not footprint_is_walkable(
        target_definition,
        target_snapshot,
        target_actor,
    ):
        raise MapMovementError("portal_target_blocked", portal_id)
    target_occupied = set(occupied_actor_cells(target_snapshot.actors))
    if footprint_overlaps(target_actor, target_occupied):
        raise MapMovementError("portal_target_occupied", portal_id)

    exit_command_id = f"{transition_id}:exit"
    enter_command_id = f"{transition_id}:enter"
    source_sequence = source_snapshot.applied_event_sequence + 1
    target_sequence = target_snapshot.applied_event_sequence + 1
    exit_event = ActorExitedMapEvent(
        event_id=f"map-event:{source_snapshot.map_instance_id}:{source_sequence}",
        command_id=exit_command_id,
        transition_id=transition_id,
        map_instance_id=source_snapshot.map_instance_id,
        actor_id=actor_id,
        from_cell=actor.cell,
        portal_id=portal_id,
        target_map_instance_id=target_snapshot.map_instance_id,
        target_cell=portal.target.cell,
        map_state_revision_before=source_snapshot.map_state_revision,
        map_state_revision_after=source_snapshot.map_state_revision + 1,
        event_sequence=source_sequence,
    )
    enter_event = ActorEnteredMapEvent(
        event_id=f"map-event:{target_snapshot.map_instance_id}:{target_sequence}",
        command_id=enter_command_id,
        transition_id=transition_id,
        map_instance_id=target_snapshot.map_instance_id,
        actor=target_actor,
        portal_id=portal_id,
        source_map_instance_id=source_snapshot.map_instance_id,
        source_cell=actor.cell,
        map_state_revision_before=target_snapshot.map_state_revision,
        map_state_revision_after=target_snapshot.map_state_revision + 1,
        event_sequence=target_sequence,
    )
    return (
        exit_event,
        enter_event,
        reduce_npc_spatial_map_event(source_snapshot, exit_event),
        reduce_npc_spatial_map_event(target_snapshot, enter_event),
    )


def reduce_npc_spatial_map_event(
    snapshot: CampaignMapInstanceSnapshot,
    event: NpcSpatialResolvedMapEvent,
) -> CampaignMapInstanceSnapshot:
    if isinstance(event, ActorMovedEvent):
        return reduce_map_event(snapshot, event)
    if event.map_instance_id != snapshot.map_instance_id:
        raise MapMovementError("event_map_instance_mismatch")
    if event.map_state_revision_before != snapshot.map_state_revision:
        raise MapMovementError("event_revision_mismatch")
    if event.event_sequence != snapshot.applied_event_sequence + 1:
        raise MapMovementError("event_sequence_mismatch")
    if isinstance(event, ActorExitedMapEvent):
        actor = snapshot.actor(event.actor_id)
        if actor.cell != event.from_cell:
            raise MapMovementError("event_actor_origin_mismatch", event.actor_id)
        actors = tuple(row for row in snapshot.actors if row.actor_id != event.actor_id)
    else:
        if any(row.actor_id == event.actor.actor_id for row in snapshot.actors):
            raise MapMovementError("event_actor_already_present", event.actor.actor_id)
        occupied = set(occupied_actor_cells(snapshot.actors))
        if footprint_overlaps(event.actor, occupied):
            raise MapMovementError(
                "event_actor_footprint_occupied",
                event.actor.actor_id,
            )
        actors = tuple(sorted((*snapshot.actors, event.actor), key=lambda row: row.actor_id))
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


