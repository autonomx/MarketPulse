from __future__ import annotations

from typing import Any

from app.apps.rpg.narration.narrative_engine import CanonicalNarrativeResponse
from app.apps.rpg.narration.narrative_engine.serialization import canonical_response_from_dict

from app.persistence.errors import RevisionConflict
from app.apps.rpg.foundation.persistence.rpg_repository import canonical_json
from app.persistence.tenant import TenantContext


class NarrativeResponsePersistenceConflict(RevisionConflict):
    pass


def _response(row: Any) -> CanonicalNarrativeResponse:
    return canonical_response_from_dict(dict(row[0]))


class PostgresRpgNarrativeResponseRepository:
    """Immutable tenant-scoped canonical response persistence."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def save(
        self,
        context: TenantContext,
        response: CanonicalNarrativeResponse,
    ) -> CanonicalNarrativeResponse:
        frozen = response.with_content_hash()
        row = self.connection.execute(
            """
            INSERT INTO omnix_rpg_narrative_responses (
                workspace_id, response_id, campaign_id, turn_id, revision,
                content_hash, canonical_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT DO NOTHING
            RETURNING canonical_jsonb
            """,
            (
                context.workspace_id,
                frozen.response_id,
                frozen.campaign_id,
                frozen.turn_id,
                frozen.revision,
                frozen.content_hash,
                canonical_json(frozen.as_dict()),
            ),
        ).fetchone()
        if row is not None:
            return _response(row)

        existing = self.get(context, frozen.response_id)
        if existing is None:
            existing = self.get_for_turn(context, frozen.campaign_id, frozen.turn_id)
        if existing is not None and existing.content_hash == frozen.content_hash:
            return existing
        raise NarrativeResponsePersistenceConflict(
            f"canonical response identity conflict: {frozen.campaign_id}/{frozen.turn_id}/{frozen.response_id}"
        )

    def get(
        self,
        context: TenantContext,
        response_id: str,
    ) -> CanonicalNarrativeResponse | None:
        row = self.connection.execute(
            """
            SELECT canonical_jsonb
              FROM omnix_rpg_narrative_responses
             WHERE workspace_id = %s AND response_id = %s
            """,
            (context.workspace_id, response_id),
        ).fetchone()
        return _response(row) if row is not None else None

    def get_for_turn(
        self,
        context: TenantContext,
        campaign_id: str,
        turn_id: str,
    ) -> CanonicalNarrativeResponse | None:
        row = self.connection.execute(
            """
            SELECT canonical_jsonb
              FROM omnix_rpg_narrative_responses
             WHERE workspace_id = %s AND campaign_id = %s AND turn_id = %s
            """,
            (context.workspace_id, campaign_id, turn_id),
        ).fetchone()
        return _response(row) if row is not None else None

    def list_campaign(
        self,
        context: TenantContext,
        campaign_id: str,
        *,
        limit: int = 500,
    ) -> tuple[CanonicalNarrativeResponse, ...]:
        rows = self.connection.execute(
            """
            SELECT canonical_jsonb
              FROM omnix_rpg_narrative_responses
             WHERE workspace_id = %s AND campaign_id = %s
             ORDER BY revision, turn_id, response_id
             LIMIT %s
            """,
            (context.workspace_id, campaign_id, max(1, min(int(limit), 2_000))),
        ).fetchall()
        return tuple(_response(row) for row in rows)
