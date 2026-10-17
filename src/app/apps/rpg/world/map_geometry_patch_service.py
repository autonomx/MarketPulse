"""Transactional services for campaign-owned geometry patch events."""
from __future__ import annotations

from typing import Any

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from app.apps.rpg.world.map_geometry_patch import (
    ApplyGeometryPatchCommand,
    MapGeometryPatchedEvent,
    resolve_geometry_patch_command,
)
from app.apps.rpg.world.map_grid_contracts import GridMapDefinition
from app.apps.rpg.world.map_instance_runtime import CampaignMapInstanceSnapshot


def apply_campaign_geometry_patch(
    map_instance_id: str,
    command: ApplyGeometryPatchCommand,
    *,
    database: Any | None = None,
) -> tuple[MapGeometryPatchedEvent, CampaignMapInstanceSnapshot]:
    context = current_tenant()
    with unit_of_work(database) as work:
        instance = work.map_instances.get_instance(
            context,
            map_instance_id,
            for_update=True,
        )
        if instance is None:
            raise KeyError(f"map_instance_not_found:{map_instance_id}")
        definition_row = work.map_instances.get_definition(
            context,
            str(instance["map_id"]),
            int(instance["definition_revision"]),
        )
        if definition_row is None:
            raise KeyError(
                f"map_definition_not_found:{instance['map_id']}:"
                f"{instance['definition_revision']}"
            )
        definition = GridMapDefinition.model_validate(definition_row["document"])
        snapshot = CampaignMapInstanceSnapshot.model_validate(instance["snapshot"])
        event, updated = resolve_geometry_patch_command(
            definition,
            snapshot,
            command,
        )
        work.map_instances.append_event(
            context,
            map_instance_id=map_instance_id,
            command_id=event.command_id,
            event_id=event.event_id,
            event_type=event.event_type,
            event_sequence=event.event_sequence,
            revision_before=event.map_state_revision_before,
            revision_after=event.map_state_revision_after,
            event=event.model_dump(mode="json"),
            snapshot=updated.model_dump(mode="json"),
        )
        work.commit()
    return event, updated
