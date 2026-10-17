"""Read-only service for measured campaign grid runtime profiles."""
from __future__ import annotations

from typing import Any

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from app.apps.rpg.world.grid_runtime_performance import (
    GridRuntimeBudget,
    profile_grid_runtime,
)
from app.apps.rpg.world.map_grid_contracts import GridMapDefinition, GridPoint
from app.apps.rpg.world.map_instance_runtime import CampaignMapInstanceSnapshot
from app.apps.rpg.world.map_observer_runtime import (
    ObserverMapKnowledge,
    ObserverPerceptionPolicy,
    observe_map,
)


def profile_campaign_grid_runtime(
    map_instance_id: str,
    *,
    observer_actor_id: str = "",
    path_probe_actor_id: str = "",
    path_probe_destination: GridPoint | None = None,
    budget: GridRuntimeBudget | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        stored_instance = work.map_instances.get_instance(context, map_instance_id)
        if stored_instance is None:
            work.rollback()
            raise KeyError(f"map_instance_not_found:{map_instance_id}")
        stored_definition = work.map_instances.get_definition(
            context,
            stored_instance["map_id"],
            stored_instance["definition_revision"],
        )
        if stored_definition is None:
            work.rollback()
            raise KeyError(
                "map_definition_not_found:"
                f"{stored_instance['map_id']}:{stored_instance['definition_revision']}"
            )
        event_row = work.connection.execute(
            "SELECT COUNT(*) FROM omnix_rpg_campaign_map_events "
            "WHERE workspace_id = %s AND map_instance_id = %s",
            (context.workspace_id, map_instance_id),
        ).fetchone()
        event_count = int(event_row[0]) if event_row is not None else 0
        definition = GridMapDefinition.model_validate(stored_definition["document"])
        snapshot = CampaignMapInstanceSnapshot.model_validate(stored_instance["snapshot"])
        knowledge = None
        knowledge_source = "none"
        if observer_actor_id:
            row = work.observers.get_knowledge(
                context,
                campaign_id=snapshot.campaign_id,
                map_instance_id=map_instance_id,
                observer_actor_id=observer_actor_id,
            )
            if row is not None:
                knowledge = _knowledge(row)
                knowledge_source = "durable"
            else:
                knowledge, _ = observe_map(
                    definition,
                    snapshot,
                    observer_actor_id=observer_actor_id,
                )
                knowledge_source = "ephemeral"
        work.rollback()

    profile = profile_grid_runtime(
        definition,
        snapshot,
        observer_knowledge=knowledge,
        observer_actor_id=observer_actor_id,
        path_probe_actor_id=path_probe_actor_id,
        path_probe_destination=path_probe_destination,
        event_count=event_count,
        budget=budget,
    )
    return {
        "ok": True,
        "knowledge_source": knowledge_source,
        "profile": profile.model_dump(mode="json"),
    }


def _knowledge(row: dict[str, Any]) -> ObserverMapKnowledge:
    return ObserverMapKnowledge(
        campaign_id=str(row["campaign_id"]),
        map_instance_id=str(row["map_instance_id"]),
        observer_actor_id=str(row["observer_actor_id"]),
        knowledge_revision=int(row["knowledge_revision"]),
        observation_sequence=int(row["observation_sequence"]),
        observed_map_state_revision=int(row["observed_map_state_revision"]),
        policy=ObserverPerceptionPolicy.model_validate(row["policy"]),
        visible_cells=tuple(row["visible_cells"]),
        known_cells=tuple(row["known_cells"]),
        detected_actor_ids=tuple(row["detected_actor_ids"]),
        known_portal_ids=tuple(row["known_portal_ids"]),
        known_spawn_point_ids=tuple(row["known_spawn_point_ids"]),
        known_zone_ids=tuple(row["known_zone_ids"]),
    )
