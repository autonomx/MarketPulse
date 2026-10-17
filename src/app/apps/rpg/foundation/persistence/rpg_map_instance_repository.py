from __future__ import annotations

from typing import Any, Mapping

from app.persistence.errors import EntityNotFound, RevisionConflict
from .rpg_repository import canonical_json
from app.persistence.tenant import TenantContext


# RPG is being retired; reads stay bounded until it is removed (WP-5.5).
MAX_ROWS_PER_READ = 10_000

class MapInstanceRevisionConflict(RevisionConflict):
    pass


class PostgresRpgMapInstanceRepository:
    """Immutable map definitions plus optimistic event/snapshot persistence."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def put_definition(
        self,
        context: TenantContext,
        *,
        map_id: str,
        definition_revision: int,
        world_id: str,
        world_revision: int,
        document: Mapping[str, Any],
        definition_hash: str,
        semantic_interface_hash: str,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            INSERT INTO omnix_rpg_map_definitions (
                workspace_id, map_id, definition_revision, world_id,
                world_revision, document_jsonb, definition_hash,
                semantic_interface_hash
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (workspace_id, map_id, definition_revision) DO NOTHING
            RETURNING map_id, definition_revision, world_id, world_revision,
                      document_jsonb, definition_hash, semantic_interface_hash,
                      created_at
            """,
            (
                context.workspace_id,
                map_id,
                int(definition_revision),
                world_id,
                int(world_revision),
                canonical_json(dict(document)),
                definition_hash,
                semantic_interface_hash,
            ),
        ).fetchone()
        if row is None:
            current = self.get_definition(context, map_id, definition_revision)
            if current is None or current["definition_hash"] != definition_hash:
                raise MapInstanceRevisionConflict(
                    f"map definition revision already exists: {map_id}:{definition_revision}"
                )
            return current
        return _definition_row(row)

    def get_definition(
        self,
        context: TenantContext,
        map_id: str,
        definition_revision: int,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT map_id, definition_revision, world_id, world_revision, "
            "document_jsonb, definition_hash, semantic_interface_hash, created_at "
            "FROM omnix_rpg_map_definitions WHERE workspace_id = %s "
            "AND map_id = %s AND definition_revision = %s",
            (context.workspace_id, map_id, int(definition_revision)),
        ).fetchone()
        return _definition_row(row) if row is not None else None

    def create_instance(
        self,
        context: TenantContext,
        *,
        map_instance_id: str,
        campaign_id: str,
        location_id: str,
        map_id: str,
        definition_revision: int,
        definition_hash: str,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        definition = self.get_definition(context, map_id, definition_revision)
        if definition is None:
            raise EntityNotFound(f"{map_id}:{definition_revision}")
        if definition["definition_hash"] != definition_hash:
            raise MapInstanceRevisionConflict("map_instance_definition_hash_mismatch")
        row = self.connection.execute(
            """
            INSERT INTO omnix_rpg_campaign_map_instances (
                workspace_id, map_instance_id, campaign_id, location_id, map_id,
                map_definition_revision, definition_hash, map_state_revision,
                applied_event_sequence, snapshot_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (workspace_id, map_instance_id) DO NOTHING
            RETURNING map_instance_id, campaign_id, location_id, map_id,
                      map_definition_revision, definition_hash,
                      map_state_revision, applied_event_sequence,
                      snapshot_jsonb, created_at, updated_at
            """,
            (
                context.workspace_id,
                map_instance_id,
                campaign_id,
                location_id,
                map_id,
                int(definition_revision),
                definition_hash,
                int(snapshot.get("map_state_revision") or 0),
                int(snapshot.get("applied_event_sequence") or 0),
                canonical_json(dict(snapshot)),
            ),
        ).fetchone()
        if row is None:
            current = self.get_instance(context, map_instance_id)
            if current is None or current["snapshot"] != dict(snapshot):
                raise MapInstanceRevisionConflict(
                    f"map instance already exists: {map_instance_id}"
                )
            return current
        return _instance_row(row)

    def get_instance(
        self,
        context: TenantContext,
        map_instance_id: str,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self.connection.execute(
            "SELECT map_instance_id, campaign_id, location_id, map_id, "
            "map_definition_revision, definition_hash, map_state_revision, "
            "applied_event_sequence, snapshot_jsonb, created_at, updated_at "
            "FROM omnix_rpg_campaign_map_instances WHERE workspace_id = %s "
            "AND map_instance_id = %s" + suffix,
            (context.workspace_id, map_instance_id),
        ).fetchone()
        return _instance_row(row) if row is not None else None

    def append_event(
        self,
        context: TenantContext,
        *,
        map_instance_id: str,
        command_id: str,
        event_id: str,
        event_type: str,
        event_sequence: int,
        revision_before: int,
        revision_after: int,
        event: Mapping[str, Any],
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        existing = self.connection.execute(
            "SELECT payload_jsonb FROM omnix_rpg_campaign_map_events "
            "WHERE workspace_id = %s AND map_instance_id = %s AND command_id = %s",
            (context.workspace_id, map_instance_id, command_id),
        ).fetchone()
        if existing is not None:
            return {"idempotent": True, "event": dict(existing[0])}
        instance = self.get_instance(context, map_instance_id, for_update=True)
        if instance is None:
            raise EntityNotFound(map_instance_id)
        if instance["map_state_revision"] != int(revision_before):
            raise MapInstanceRevisionConflict(
                f"map instance {map_instance_id} expected revision "
                f"{revision_before}; current {instance['map_state_revision']}"
            )
        if instance["applied_event_sequence"] + 1 != int(event_sequence):
            raise MapInstanceRevisionConflict("map_event_sequence_mismatch")
        self.connection.execute(
            """
            INSERT INTO omnix_rpg_campaign_map_events (
                workspace_id, map_instance_id, event_sequence, event_id,
                command_id, event_type, map_state_revision_before,
                map_state_revision_after, payload_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                context.workspace_id,
                map_instance_id,
                int(event_sequence),
                event_id,
                command_id,
                event_type,
                int(revision_before),
                int(revision_after),
                canonical_json(dict(event)),
            ),
        )
        updated = self.connection.execute(
            """
            UPDATE omnix_rpg_campaign_map_instances
               SET map_state_revision = %s,
                   applied_event_sequence = %s,
                   snapshot_jsonb = %s::jsonb,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND map_instance_id = %s
               AND map_state_revision = %s
            RETURNING map_instance_id, campaign_id, location_id, map_id,
                      map_definition_revision, definition_hash,
                      map_state_revision, applied_event_sequence,
                      snapshot_jsonb, created_at, updated_at
            """,
            (
                int(revision_after),
                int(event_sequence),
                canonical_json(dict(snapshot)),
                context.workspace_id,
                map_instance_id,
                int(revision_before),
            ),
        ).fetchone()
        if updated is None:
            raise MapInstanceRevisionConflict("map_instance_compare_and_swap_failed")
        return {
            "idempotent": False,
            "event": dict(event),
            "instance": _instance_row(updated),
        }

    def list_events(
        self,
        context: TenantContext,
        map_instance_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT payload_jsonb FROM omnix_rpg_campaign_map_events "
            "WHERE workspace_id = %s AND map_instance_id = %s "
            "AND event_sequence > %s ORDER BY event_sequence LIMIT %s",
            (context.workspace_id, map_instance_id, int(after_sequence), MAX_ROWS_PER_READ),
        ).fetchall()
        return [dict(row[0]) for row in rows]


def _definition_row(row: Any) -> dict[str, Any]:
    return {
        "map_id": str(row[0]),
        "definition_revision": int(row[1]),
        "world_id": str(row[2]),
        "world_revision": int(row[3]),
        "document": dict(row[4]),
        "definition_hash": str(row[5]),
        "semantic_interface_hash": str(row[6]),
        "created_at": row[7].isoformat(),
    }


def _instance_row(row: Any) -> dict[str, Any]:
    return {
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
