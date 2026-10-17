from __future__ import annotations

from typing import Any

from app.apps.rpg.world.map_observer_runtime import (
    ObserverMapKnowledge,
    ObserverMapObservedEvent,
)

from app.persistence.errors import RevisionConflict
from app.apps.rpg.foundation.persistence.rpg_repository import canonical_json
from app.persistence.tenant import TenantContext
from app.runtime.pagination import page_limit


class ObserverKnowledgeRevisionConflict(RevisionConflict):
    pass


def _cells(value: Any) -> tuple[tuple[int, int], ...]:
    return tuple((int(row[0]), int(row[1])) for row in value or ())


def _knowledge_row(row: Any) -> dict[str, Any]:
    return {
        "campaign_id": str(row[0]),
        "map_instance_id": str(row[1]),
        "observer_actor_id": str(row[2]),
        "knowledge_revision": int(row[3]),
        "observation_sequence": int(row[4]),
        "observed_map_state_revision": int(row[5]),
        "policy": dict(row[6]),
        "visible_cells": _cells(row[7]),
        "known_cells": _cells(row[8]),
        "detected_actor_ids": tuple(str(item) for item in row[9]),
        "known_portal_ids": tuple(str(item) for item in row[10]),
        "known_spawn_point_ids": tuple(str(item) for item in row[11]),
        "known_zone_ids": tuple(str(item) for item in row[12]),
        "created_at": row[13].isoformat(),
        "updated_at": row[14].isoformat(),
    }


_KNOWLEDGE_COLUMNS = """
campaign_id, map_instance_id, observer_actor_id, knowledge_revision,
observation_sequence, observed_map_state_revision, policy_jsonb,
visible_cells_jsonb, known_cells_jsonb, detected_actor_ids_jsonb,
known_portal_ids_jsonb, known_spawn_point_ids_jsonb, known_zone_ids_jsonb,
created_at, updated_at
"""


class PostgresRpgObserverRepository:
    """Durable observer-owned knowledge derived from authoritative map snapshots."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get_knowledge(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        map_instance_id: str,
        observer_actor_id: str,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self.connection.execute(
            f"SELECT {_KNOWLEDGE_COLUMNS} FROM omnix_rpg_map_observer_knowledge "
            "WHERE workspace_id = %s AND campaign_id = %s "
            "AND map_instance_id = %s AND observer_actor_id = %s" + suffix,
            (
                context.workspace_id,
                campaign_id,
                map_instance_id,
                observer_actor_id,
            ),
        ).fetchone()
        return _knowledge_row(row) if row is not None else None

    def put_observation(
        self,
        context: TenantContext,
        *,
        knowledge: ObserverMapKnowledge,
        event: ObserverMapObservedEvent,
        expected_knowledge_revision: int,
    ) -> dict[str, Any]:
        existing = self.get_knowledge(
            context,
            campaign_id=knowledge.campaign_id,
            map_instance_id=knowledge.map_instance_id,
            observer_actor_id=knowledge.observer_actor_id,
            for_update=True,
        )
        if existing is None:
            if expected_knowledge_revision != 0 or knowledge.knowledge_revision != 1:
                raise ObserverKnowledgeRevisionConflict(
                    "observer_knowledge_expected_new:"
                    f"{knowledge.map_instance_id}:{knowledge.observer_actor_id}"
                )
            row = self.connection.execute(
                f"""
                INSERT INTO omnix_rpg_map_observer_knowledge (
                    workspace_id, campaign_id, map_instance_id, observer_actor_id,
                    knowledge_revision, observation_sequence,
                    observed_map_state_revision, policy_jsonb,
                    visible_cells_jsonb, known_cells_jsonb,
                    detected_actor_ids_jsonb, known_portal_ids_jsonb,
                    known_spawn_point_ids_jsonb, known_zone_ids_jsonb
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                          %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
                RETURNING {_KNOWLEDGE_COLUMNS}
                """,
                self._knowledge_params(context, knowledge),
            ).fetchone()
        else:
            if int(existing["knowledge_revision"]) != int(expected_knowledge_revision):
                raise ObserverKnowledgeRevisionConflict(
                    "observer_knowledge_revision_conflict:"
                    f"{knowledge.map_instance_id}:{knowledge.observer_actor_id}:"
                    f"{expected_knowledge_revision}:{existing['knowledge_revision']}"
                )
            row = self.connection.execute(
                f"""
                UPDATE omnix_rpg_map_observer_knowledge
                   SET knowledge_revision = %s, observation_sequence = %s,
                       observed_map_state_revision = %s, policy_jsonb = %s::jsonb,
                       visible_cells_jsonb = %s::jsonb,
                       known_cells_jsonb = %s::jsonb,
                       detected_actor_ids_jsonb = %s::jsonb,
                       known_portal_ids_jsonb = %s::jsonb,
                       known_spawn_point_ids_jsonb = %s::jsonb,
                       known_zone_ids_jsonb = %s::jsonb,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND campaign_id = %s
                   AND map_instance_id = %s AND observer_actor_id = %s
                   AND knowledge_revision = %s
                RETURNING {_KNOWLEDGE_COLUMNS}
                """,
                (
                    knowledge.knowledge_revision,
                    knowledge.observation_sequence,
                    knowledge.observed_map_state_revision,
                    canonical_json(knowledge.policy.model_dump(mode="json")),
                    canonical_json(knowledge.visible_cells),
                    canonical_json(knowledge.known_cells),
                    canonical_json(knowledge.detected_actor_ids),
                    canonical_json(knowledge.known_portal_ids),
                    canonical_json(knowledge.known_spawn_point_ids),
                    canonical_json(knowledge.known_zone_ids),
                    context.workspace_id,
                    knowledge.campaign_id,
                    knowledge.map_instance_id,
                    knowledge.observer_actor_id,
                    expected_knowledge_revision,
                ),
            ).fetchone()
            if row is None:
                raise ObserverKnowledgeRevisionConflict(
                    "observer_knowledge_compare_and_swap_failed:"
                    f"{knowledge.map_instance_id}:{knowledge.observer_actor_id}"
                )
        self.connection.execute(
            """
            INSERT INTO omnix_rpg_map_observation_events (
                workspace_id, campaign_id, map_instance_id, observer_actor_id,
                observation_sequence, event_id, map_state_revision, event_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                context.workspace_id,
                event.campaign_id,
                event.map_instance_id,
                event.observer_actor_id,
                event.observation_sequence,
                event.event_id,
                event.map_state_revision,
                canonical_json(event.model_dump(mode="json")),
            ),
        )
        return _knowledge_row(row)

    def _knowledge_params(
        self,
        context: TenantContext,
        knowledge: ObserverMapKnowledge,
    ) -> tuple[Any, ...]:
        return (
            context.workspace_id,
            knowledge.campaign_id,
            knowledge.map_instance_id,
            knowledge.observer_actor_id,
            knowledge.knowledge_revision,
            knowledge.observation_sequence,
            knowledge.observed_map_state_revision,
            canonical_json(knowledge.policy.model_dump(mode="json")),
            canonical_json(knowledge.visible_cells),
            canonical_json(knowledge.known_cells),
            canonical_json(knowledge.detected_actor_ids),
            canonical_json(knowledge.known_portal_ids),
            canonical_json(knowledge.known_spawn_point_ids),
            canonical_json(knowledge.known_zone_ids),
        )

    def list_events(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        map_instance_id: str,
        observer_actor_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT event_jsonb FROM omnix_rpg_map_observation_events "
            "WHERE workspace_id = %s AND campaign_id = %s "
            "AND map_instance_id = %s AND observer_actor_id = %s "
            "ORDER BY observation_sequence DESC LIMIT %s",
            (
                context.workspace_id,
                campaign_id,
                map_instance_id,
                observer_actor_id,
                page_limit(limit, default=100),
            ),
        ).fetchall()
        return [dict(row[0]) for row in rows]
