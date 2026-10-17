"""Authoring and read services for durable campaign NPC spatial state."""
from __future__ import annotations

from typing import Any

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from app.apps.rpg.world.map_grid_contracts import GridMapDefinition
from app.apps.rpg.world.map_instance_runtime import CampaignMapInstanceSnapshot
from app.apps.rpg.world.npc_spatial_campaign_contracts import (
    CampaignNpcSpatialGoal,
    CampaignNpcSpatialPolicy,
    CampaignNpcSpatialRoutine,
)


def _database(value: Any | None) -> Any | None:
    return value


def _instance_and_definition(
    work: Any,
    context: Any,
    *,
    campaign_id: str,
    map_instance_id: str,
) -> tuple[CampaignMapInstanceSnapshot, GridMapDefinition]:
    row = work.map_instances.get_instance(context, map_instance_id)
    if row is None:
        raise KeyError(f"map_instance_not_found:{map_instance_id}")
    if str(row["campaign_id"]) != campaign_id:
        raise ValueError(f"map_instance_campaign_mismatch:{map_instance_id}")
    definition_row = work.map_instances.get_definition(
        context,
        str(row["map_id"]),
        int(row["definition_revision"]),
    )
    if definition_row is None:
        raise KeyError(
            f"map_definition_not_found:{row['map_id']}:{row['definition_revision']}"
        )
    return (
        CampaignMapInstanceSnapshot.model_validate(row["snapshot"]),
        GridMapDefinition.model_validate(definition_row["document"]),
    )


def _validate_goal(
    work: Any,
    context: Any,
    goal: CampaignNpcSpatialGoal,
    *,
    require_actor: bool = True,
) -> None:
    snapshot, definition = _instance_and_definition(
        work,
        context,
        campaign_id=goal.campaign_id,
        map_instance_id=goal.map_instance_id,
    )
    if require_actor:
        snapshot.actor(goal.actor_id)
    if goal.goal_type == "move_to_cell":
        assert goal.target_cell is not None
        definition.require_inside(goal.target_cell)
        if not definition.is_walkable(goal.target_cell):
            raise ValueError(f"npc_spatial_goal_target_blocked:{goal.goal_id}")
        return
    assert goal.portal_id is not None
    assert goal.target_map_instance_id is not None
    _, target_definition = _instance_and_definition(
        work,
        context,
        campaign_id=goal.campaign_id,
        map_instance_id=goal.target_map_instance_id,
    )
    portal = next(
        (row for row in definition.portals if row.portal_id == goal.portal_id),
        None,
    )
    if portal is None:
        raise ValueError(f"npc_spatial_goal_portal_missing:{goal.portal_id}")
    if portal.source.map_id != definition.map_id:
        raise ValueError(f"npc_spatial_goal_portal_direction_invalid:{goal.portal_id}")
    if portal.target.map_id != target_definition.map_id:
        raise ValueError(f"npc_spatial_goal_portal_target_mismatch:{goal.portal_id}")


def save_campaign_spatial_goal(
    goal: CampaignNpcSpatialGoal,
    *,
    expected_revision: int = 0,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        _validate_goal(work, context, goal)
        stored = work.npc_spatial.put_goal(
            context,
            goal,
            expected_revision=expected_revision,
        )
        work.commit()
    return {"ok": True, "goal": stored}


def save_campaign_spatial_routine(
    routine: CampaignNpcSpatialRoutine,
    *,
    expected_revision: int = 0,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        for step in routine.steps:
            validation_goal = CampaignNpcSpatialGoal(
                goal_id=f"routine-validation:{routine.routine_id}:{step.step_id}",
                campaign_id=routine.campaign_id,
                actor_id=routine.actor_id,
                map_instance_id=step.map_instance_id,
                goal_type=step.goal_type,
                target_cell=step.target_cell,
                portal_id=step.portal_id,
                target_map_instance_id=step.target_map_instance_id,
            )
            _validate_goal(
                work,
                context,
                validation_goal,
                require_actor=False,
            )
        stored = work.npc_spatial.put_routine(
            context,
            routine,
            expected_revision=expected_revision,
        )
        work.commit()
    return {"ok": True, "routine": stored}


def configure_campaign_spatial_policy(
    campaign_id: str,
    policy: CampaignNpcSpatialPolicy,
    *,
    expected_world_tick: int,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        clock = work.npc_spatial.clock_for_update(
            context,
            campaign_id,
            default_policy=policy,
        )
        if int(clock["world_tick"]) != int(expected_world_tick):
            raise ValueError(
                f"campaign_spatial_tick_conflict:{campaign_id}:"
                f"{expected_world_tick}:{clock['world_tick']}"
            )
        stored = work.npc_spatial.update_clock(
            context,
            campaign_id=campaign_id,
            expected_world_tick=expected_world_tick,
            world_tick=expected_world_tick,
            policy=policy.model_dump(mode="json"),
            aggregate_metrics=clock["aggregate_metrics"],
        )
        work.commit()
    return {"ok": True, "clock": stored}


def read_campaign_spatial_state(
    campaign_id: str,
    *,
    database: Any | None = None,
    tick_limit: int = 50,
) -> dict[str, Any]:
    context = current_tenant()
    default_policy = CampaignNpcSpatialPolicy()
    with unit_of_work(database) as work:
        clock = work.npc_spatial.clock_for_update(
            context,
            campaign_id,
            default_policy=default_policy,
        )
        goals = work.npc_spatial.list_active_goals(context, campaign_id)
        routines = work.connection.execute(
            "SELECT routine_id, routine_revision, actor_id, enabled, interval_ticks, "
            "document_jsonb, next_step_index, emission_count, next_due_tick, "
            "last_issued_tick, metadata_jsonb, created_at, updated_at "
            "FROM omnix_rpg_npc_spatial_routines WHERE workspace_id = %s "
            "AND campaign_id = %s ORDER BY routine_id",
            (context.workspace_id, campaign_id),
        ).fetchall()
        ticks = work.npc_spatial.recent_ticks(
            context,
            campaign_id,
            limit=tick_limit,
        )
        work.rollback()
    return {
        "ok": True,
        "clock": clock,
        "goals": goals,
        "routines": [
            {
                "routine_id": str(row[0]),
                "routine_revision": int(row[1]),
                "actor_id": str(row[2]),
                "enabled": bool(row[3]),
                "interval_ticks": int(row[4]),
                "steps": list(dict(row[5]).get("steps") or ()),
                "next_step_index": int(row[6]),
                "emission_count": int(row[7]),
                "next_due_tick": int(row[8]),
                "last_issued_tick": int(row[9]) if row[9] is not None else None,
                "metadata": dict(row[10]),
                "created_at": row[11].isoformat(),
                "updated_at": row[12].isoformat(),
            }
            for row in routines
        ],
        "recent_ticks": ticks,
    }
