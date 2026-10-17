from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.apps.rpg.world.npc_spatial_campaign_contracts import (
    CampaignNpcSpatialGoal,
    CampaignNpcSpatialPolicy,
    CampaignNpcSpatialRoutine,
)

from app.persistence.errors import EntityNotFound, RevisionConflict
from app.apps.rpg.foundation.persistence.rpg_repository import canonical_json
from app.persistence.tenant import TenantContext


# RPG is being retired; reads stay bounded until it is removed (WP-5.5).
MAX_ROWS_PER_READ = 10_000

class NpcSpatialRevisionConflict(RevisionConflict):
    pass


def _clock_row(row: Any) -> dict[str, Any]:
    return {
        "campaign_id": str(row[0]),
        "world_tick": int(row[1]),
        "policy": dict(row[2]),
        "aggregate_metrics": dict(row[3]),
        "created_at": row[4].isoformat(),
        "updated_at": row[5].isoformat(),
    }


def _goal_row(row: Any) -> dict[str, Any]:
    target_cell = tuple(row[6]) if row[6] is not None else None
    return {
        "goal_id": str(row[0]),
        "goal_revision": int(row[1]),
        "campaign_id": str(row[2]),
        "actor_id": str(row[3]),
        "map_instance_id": str(row[4]),
        "goal_type": str(row[5]),
        "target_cell": target_cell,
        "portal_id": str(row[7]) if row[7] is not None else None,
        "target_map_instance_id": str(row[8]) if row[8] is not None else None,
        "priority": int(row[9]),
        "issued_tick": int(row[10]),
        "not_before_tick": int(row[11]),
        "expires_after_tick": int(row[12]) if row[12] is not None else None,
        "status": str(row[13]),
        "routine_id": str(row[14]) if row[14] is not None else None,
        "blocked_attempts": int(row[15]),
        "last_decision": dict(row[16]),
        "metadata": dict(row[17]),
        "created_at": row[18].isoformat(),
        "updated_at": row[19].isoformat(),
        "completed_at": row[20].isoformat() if row[20] is not None else None,
    }


def _routine_row(row: Any) -> dict[str, Any]:
    document = dict(row[6])
    return {
        "routine_id": str(row[0]),
        "routine_revision": int(row[1]),
        "campaign_id": str(row[2]),
        "actor_id": str(row[3]),
        "enabled": bool(row[4]),
        "interval_ticks": int(row[5]),
        "steps": list(document.get("steps") or ()),
        "next_step_index": int(row[7]),
        "emission_count": int(row[8]),
        "next_due_tick": int(row[9]),
        "last_issued_tick": int(row[10]) if row[10] is not None else None,
        "metadata": dict(row[11]),
        "created_at": row[12].isoformat(),
        "updated_at": row[13].isoformat(),
    }


_GOAL_COLUMNS = """
goal_id, goal_revision, campaign_id, actor_id, map_instance_id, goal_type,
target_cell_jsonb, portal_id, target_map_instance_id, priority, issued_tick,
not_before_tick, expires_after_tick, status, routine_id, blocked_attempts,
last_decision_jsonb, metadata_jsonb, created_at, updated_at, completed_at
"""
_ROUTINE_COLUMNS = """
routine_id, routine_revision, campaign_id, actor_id, enabled, interval_ticks,
document_jsonb, next_step_index, emission_count, next_due_tick,
last_issued_tick, metadata_jsonb, created_at, updated_at
"""


class PostgresRpgNpcSpatialRepository:
    """PostgreSQL authority for campaign clocks, NPC goals, routines, and metrics."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def clock_for_update(
        self,
        context: TenantContext,
        campaign_id: str,
        *,
        default_policy: CampaignNpcSpatialPolicy,
    ) -> dict[str, Any]:
        campaign = self.connection.execute(
            "SELECT 1 FROM omnix_rpg_campaigns WHERE workspace_id = %s AND id = %s",
            (context.workspace_id, campaign_id),
        ).fetchone()
        if campaign is None:
            raise EntityNotFound(campaign_id)
        self.connection.execute(
            """
            INSERT INTO omnix_rpg_campaign_spatial_clocks (
                workspace_id, campaign_id, policy_jsonb
            ) VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (workspace_id, campaign_id) DO NOTHING
            """,
            (
                context.workspace_id,
                campaign_id,
                canonical_json(default_policy.model_dump(mode="json")),
            ),
        )
        row = self.connection.execute(
            "SELECT campaign_id, world_tick, policy_jsonb, aggregate_metrics_jsonb, "
            "created_at, updated_at FROM omnix_rpg_campaign_spatial_clocks "
            "WHERE workspace_id = %s AND campaign_id = %s FOR UPDATE",
            (context.workspace_id, campaign_id),
        ).fetchone()
        return _clock_row(row)

    def update_clock(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        expected_world_tick: int,
        world_tick: int,
        policy: Mapping[str, Any],
        aggregate_metrics: Mapping[str, Any],
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            UPDATE omnix_rpg_campaign_spatial_clocks
               SET world_tick = %s, policy_jsonb = %s::jsonb,
                   aggregate_metrics_jsonb = %s::jsonb,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND campaign_id = %s AND world_tick = %s
            RETURNING campaign_id, world_tick, policy_jsonb,
                      aggregate_metrics_jsonb, created_at, updated_at
            """,
            (
                int(world_tick),
                canonical_json(dict(policy)),
                canonical_json(dict(aggregate_metrics)),
                context.workspace_id,
                campaign_id,
                int(expected_world_tick),
            ),
        ).fetchone()
        if row is None:
            raise NpcSpatialRevisionConflict(
                f"campaign_spatial_tick_conflict:{campaign_id}:{expected_world_tick}"
            )
        return _clock_row(row)

    def put_goal(
        self,
        context: TenantContext,
        goal: CampaignNpcSpatialGoal,
        *,
        expected_revision: int = 0,
    ) -> dict[str, Any]:
        existing = self.get_goal(context, goal.campaign_id, goal.goal_id, for_update=True)
        payload = goal.model_dump(mode="json")
        if existing is None:
            if expected_revision != 0 or goal.goal_revision != 1:
                raise NpcSpatialRevisionConflict(
                    f"npc_spatial_goal_expected_new:{goal.goal_id}"
                )
            row = self.connection.execute(
                f"""
                INSERT INTO omnix_rpg_npc_spatial_goals (
                    workspace_id, campaign_id, goal_id, goal_revision, actor_id,
                    map_instance_id, goal_type, target_cell_jsonb, portal_id,
                    target_map_instance_id, priority, issued_tick, not_before_tick,
                    expires_after_tick, status, routine_id, blocked_attempts,
                    last_decision_jsonb, metadata_jsonb
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                          %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                RETURNING {_GOAL_COLUMNS}
                """,
                self._goal_params(context, goal),
            ).fetchone()
            return _goal_row(row)
        if int(existing["goal_revision"]) != int(expected_revision):
            raise NpcSpatialRevisionConflict(
                f"npc_spatial_goal_revision_conflict:{goal.goal_id}:"
                f"{expected_revision}:{existing['goal_revision']}"
            )
        comparable = {
            key: existing.get(key)
            for key in payload
            if key not in {"goal_revision", "last_decision", "blocked_attempts"}
        }
        desired = {
            key: payload.get(key)
            for key in comparable
        }
        if comparable == desired and goal.goal_revision == existing["goal_revision"]:
            return existing
        next_revision = int(expected_revision) + 1
        updated_goal = goal.model_copy(update={"goal_revision": next_revision})
        row = self.connection.execute(
            f"""
            UPDATE omnix_rpg_npc_spatial_goals
               SET goal_revision = %s, actor_id = %s, map_instance_id = %s,
                   goal_type = %s, target_cell_jsonb = %s::jsonb,
                   portal_id = %s, target_map_instance_id = %s, priority = %s,
                   issued_tick = %s, not_before_tick = %s,
                   expires_after_tick = %s, status = %s, routine_id = %s,
                   metadata_jsonb = %s::jsonb, updated_at = CURRENT_TIMESTAMP,
                   completed_at = CASE WHEN %s = 'active' THEN NULL ELSE completed_at END
             WHERE workspace_id = %s AND campaign_id = %s AND goal_id = %s
               AND goal_revision = %s
            RETURNING {_GOAL_COLUMNS}
            """,
            (
                next_revision,
                updated_goal.actor_id,
                updated_goal.map_instance_id,
                updated_goal.goal_type,
                canonical_json(updated_goal.target_cell)
                if updated_goal.target_cell is not None
                else None,
                updated_goal.portal_id,
                updated_goal.target_map_instance_id,
                updated_goal.priority,
                updated_goal.issued_tick,
                updated_goal.not_before_tick,
                updated_goal.expires_after_tick,
                updated_goal.status,
                updated_goal.routine_id,
                canonical_json(updated_goal.metadata),
                updated_goal.status,
                context.workspace_id,
                updated_goal.campaign_id,
                updated_goal.goal_id,
                expected_revision,
            ),
        ).fetchone()
        if row is None:
            raise NpcSpatialRevisionConflict(
                f"npc_spatial_goal_compare_and_swap_failed:{goal.goal_id}"
            )
        return _goal_row(row)

    def _goal_params(
        self,
        context: TenantContext,
        goal: CampaignNpcSpatialGoal,
    ) -> tuple[Any, ...]:
        return (
            context.workspace_id,
            goal.campaign_id,
            goal.goal_id,
            goal.goal_revision,
            goal.actor_id,
            goal.map_instance_id,
            goal.goal_type,
            canonical_json(goal.target_cell) if goal.target_cell is not None else None,
            goal.portal_id,
            goal.target_map_instance_id,
            goal.priority,
            goal.issued_tick,
            goal.not_before_tick,
            goal.expires_after_tick,
            goal.status,
            goal.routine_id,
            goal.blocked_attempts,
            canonical_json(goal.last_decision),
            canonical_json(goal.metadata),
        )

    def get_goal(
        self,
        context: TenantContext,
        campaign_id: str,
        goal_id: str,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self.connection.execute(
            f"SELECT {_GOAL_COLUMNS} FROM omnix_rpg_npc_spatial_goals "
            "WHERE workspace_id = %s AND campaign_id = %s AND goal_id = %s" + suffix,
            (context.workspace_id, campaign_id, goal_id),
        ).fetchone()
        return _goal_row(row) if row is not None else None

    def list_active_goals(
        self,
        context: TenantContext,
        campaign_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            f"SELECT {_GOAL_COLUMNS} FROM omnix_rpg_npc_spatial_goals "
            "WHERE workspace_id = %s AND campaign_id = %s AND status = 'active' "
            "ORDER BY actor_id, priority DESC, issued_tick, goal_id LIMIT %s",
            (context.workspace_id, campaign_id, MAX_ROWS_PER_READ),
        ).fetchall()
        return [_goal_row(row) for row in rows]

    def record_goal_decision(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        goal_id: str,
        decision: Mapping[str, Any],
        status: str | None = None,
        increment_blocked: bool = False,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            UPDATE omnix_rpg_npc_spatial_goals
               SET last_decision_jsonb = %s::jsonb,
                   status = COALESCE(%s, status),
                   blocked_attempts = blocked_attempts + %s,
                   completed_at = CASE
                       WHEN COALESCE(%s, status) IN (
                           'completed', 'blocked', 'canceled', 'expired'
                       ) THEN COALESCE(completed_at, CURRENT_TIMESTAMP)
                       ELSE completed_at
                   END,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND campaign_id = %s AND goal_id = %s
            RETURNING {_GOAL_COLUMNS}
            """,
            (
                canonical_json(dict(decision)),
                status,
                1 if increment_blocked else 0,
                status,
                context.workspace_id,
                campaign_id,
                goal_id,
            ),
        ).fetchone()
        if row is None:
            raise EntityNotFound(goal_id)
        return _goal_row(row)

    def put_routine(
        self,
        context: TenantContext,
        routine: CampaignNpcSpatialRoutine,
        *,
        expected_revision: int = 0,
    ) -> dict[str, Any]:
        existing = self.connection.execute(
            f"SELECT {_ROUTINE_COLUMNS} FROM omnix_rpg_npc_spatial_routines "
            "WHERE workspace_id = %s AND campaign_id = %s AND routine_id = %s "
            "FOR UPDATE",
            (context.workspace_id, routine.campaign_id, routine.routine_id),
        ).fetchone()
        if existing is None:
            if expected_revision != 0 or routine.routine_revision != 1:
                raise NpcSpatialRevisionConflict(
                    f"npc_spatial_routine_expected_new:{routine.routine_id}"
                )
            row = self.connection.execute(
                f"""
                INSERT INTO omnix_rpg_npc_spatial_routines (
                    workspace_id, campaign_id, routine_id, routine_revision,
                    actor_id, enabled, interval_ticks, document_jsonb,
                    next_step_index, emission_count, next_due_tick,
                    last_issued_tick, metadata_jsonb
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                          %s, %s, %s::jsonb)
                RETURNING {_ROUTINE_COLUMNS}
                """,
                (
                    context.workspace_id,
                    routine.campaign_id,
                    routine.routine_id,
                    routine.routine_revision,
                    routine.actor_id,
                    routine.enabled,
                    routine.interval_ticks,
                    canonical_json({"steps": [step.model_dump(mode="json") for step in routine.steps]}),
                    routine.next_step_index,
                    routine.emission_count,
                    routine.next_due_tick,
                    routine.last_issued_tick,
                    canonical_json(routine.metadata),
                ),
            ).fetchone()
            return _routine_row(row)
        current = _routine_row(existing)
        if current["routine_revision"] != expected_revision:
            raise NpcSpatialRevisionConflict(
                f"npc_spatial_routine_revision_conflict:{routine.routine_id}"
            )
        next_revision = expected_revision + 1
        row = self.connection.execute(
            f"""
            UPDATE omnix_rpg_npc_spatial_routines
               SET routine_revision = %s, actor_id = %s, enabled = %s,
                   interval_ticks = %s, document_jsonb = %s::jsonb,
                   next_step_index = %s, emission_count = %s,
                   next_due_tick = %s, last_issued_tick = %s,
                   metadata_jsonb = %s::jsonb, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND campaign_id = %s AND routine_id = %s
               AND routine_revision = %s
            RETURNING {_ROUTINE_COLUMNS}
            """,
            (
                next_revision,
                routine.actor_id,
                routine.enabled,
                routine.interval_ticks,
                canonical_json({"steps": [step.model_dump(mode="json") for step in routine.steps]}),
                routine.next_step_index,
                routine.emission_count,
                routine.next_due_tick,
                routine.last_issued_tick,
                canonical_json(routine.metadata),
                context.workspace_id,
                routine.campaign_id,
                routine.routine_id,
                expected_revision,
            ),
        ).fetchone()
        if row is None:
            raise NpcSpatialRevisionConflict(
                f"npc_spatial_routine_compare_and_swap_failed:{routine.routine_id}"
            )
        return _routine_row(row)

    def list_due_routines(
        self,
        context: TenantContext,
        campaign_id: str,
        world_tick: int,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            f"SELECT {_ROUTINE_COLUMNS} FROM omnix_rpg_npc_spatial_routines "
            "WHERE workspace_id = %s AND campaign_id = %s AND enabled = TRUE "
            "AND next_due_tick <= %s ORDER BY routine_id LIMIT %s FOR UPDATE",
            (context.workspace_id, campaign_id, int(world_tick), MAX_ROWS_PER_READ),
        ).fetchall()
        return [_routine_row(row) for row in rows]

    def advance_routine(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        routine_id: str,
        next_step_index: int,
        emission_count: int,
        next_due_tick: int,
        world_tick: int,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            UPDATE omnix_rpg_npc_spatial_routines
               SET next_step_index = %s, emission_count = %s,
                   next_due_tick = %s, last_issued_tick = %s,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND campaign_id = %s AND routine_id = %s
            RETURNING {_ROUTINE_COLUMNS}
            """,
            (
                int(next_step_index),
                int(emission_count),
                int(next_due_tick),
                int(world_tick),
                context.workspace_id,
                campaign_id,
                routine_id,
            ),
        ).fetchone()
        if row is None:
            raise EntityNotFound(routine_id)
        return _routine_row(row)

    def list_campaign_instances(
        self,
        context: TenantContext,
        campaign_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT map_instance_id, campaign_id, location_id, map_id, "
            "map_definition_revision, definition_hash, map_state_revision, "
            "applied_event_sequence, snapshot_jsonb, created_at, updated_at "
            "FROM omnix_rpg_campaign_map_instances WHERE workspace_id = %s "
            "AND campaign_id = %s ORDER BY map_instance_id LIMIT %s FOR UPDATE",
            (context.workspace_id, campaign_id, MAX_ROWS_PER_READ),
        ).fetchall()
        return [
            {
                "map_instance_id": str(row[0]),
                "campaign_id": str(row[1]),
                "location_id": str(row[2]),
                "map_id": str(row[3]),
                "definition_revision": int(row[4]),
                "definition_hash": str(row[5]),
                "map_state_revision": int(row[6]),
                "applied_event_sequence": int(row[7]),
                "snapshot": dict(row[8]),
                "created_at": row[9].isoformat(),
                "updated_at": row[10].isoformat(),
            }
            for row in rows
        ]

    def record_transition(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        transition_id: str,
        world_tick: int,
        actor_id: str,
        portal_id: str,
        source_map_instance_id: str,
        target_map_instance_id: str,
        source_event_id: str,
        target_event_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO omnix_rpg_npc_spatial_transitions (
                workspace_id, campaign_id, transition_id, world_tick, actor_id,
                portal_id, source_map_instance_id, target_map_instance_id,
                source_event_id, target_event_id, payload_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (workspace_id, campaign_id, transition_id) DO NOTHING
            """,
            (
                context.workspace_id,
                campaign_id,
                transition_id,
                int(world_tick),
                actor_id,
                portal_id,
                source_map_instance_id,
                target_map_instance_id,
                source_event_id,
                target_event_id,
                canonical_json(dict(payload)),
            ),
        )

    def record_tick(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        world_tick: int,
        result: Mapping[str, Any],
        metrics: Mapping[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO omnix_rpg_npc_spatial_tick_runs (
                workspace_id, campaign_id, world_tick, result_jsonb, metrics_jsonb
            ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
            """,
            (
                context.workspace_id,
                campaign_id,
                int(world_tick),
                canonical_json(dict(result)),
                canonical_json(dict(metrics)),
            ),
        )

    def recent_ticks(
        self,
        context: TenantContext,
        campaign_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT world_tick, result_jsonb, metrics_jsonb, created_at "
            "FROM omnix_rpg_npc_spatial_tick_runs WHERE workspace_id = %s "
            "AND campaign_id = %s ORDER BY world_tick DESC LIMIT %s",
            (context.workspace_id, campaign_id, max(1, min(int(limit), 500))),
        ).fetchall()
        return [
            {
                "world_tick": int(row[0]),
                "result": dict(row[1]),
                "metrics": dict(row[2]),
                "created_at": row[3].isoformat(),
            }
            for row in rows
        ]
