from __future__ import annotations

import hashlib
from typing import Any, Mapping

from app.persistence.errors import EntityNotFound, RevisionConflict
from .rpg_repository import canonical_json
from app.persistence.tenant import TenantContext


class CampaignBibleRevisionConflict(RevisionConflict):
    pass


def campaign_bible_hash(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(document)).encode("utf-8")).hexdigest()


def _row(value: Any) -> dict[str, Any]:
    return {
        "workspace_id": str(value[0]),
        "campaign_id": str(value[1]),
        "revision": int(value[2]),
        "document": dict(value[3]),
        "content_hash": str(value[4]),
        "provenance": dict(value[5]),
        "consistency_report": dict(value[6]),
        "completeness": dict(value[7]),
        "created_at": value[8].isoformat(),
        "updated_at": value[9].isoformat(),
    }


_COLUMNS = """
workspace_id, campaign_id, revision, document_jsonb, content_hash,
provenance_jsonb, consistency_report_jsonb, completeness_jsonb,
created_at, updated_at
"""


class PostgresRpgCampaignBibleRepository:
    """Revisioned PostgreSQL authority for Campaign Bible aggregates."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get(
        self,
        context: TenantContext,
        campaign_id: str,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self.connection.execute(
            f"SELECT {_COLUMNS} FROM omnix_rpg_campaign_bibles "
            f"WHERE workspace_id = %s AND campaign_id = %s{suffix}",
            (context.workspace_id, campaign_id),
        ).fetchone()
        return _row(row) if row is not None else None

    def put(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        document: Mapping[str, Any],
        expected_revision: int | None,
        provenance: Mapping[str, Any] | None = None,
        consistency_report: Mapping[str, Any] | None = None,
        completeness: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        campaign = self.connection.execute(
            "SELECT id FROM omnix_rpg_campaigns "
            "WHERE workspace_id = %s AND id = %s FOR UPDATE",
            (context.workspace_id, campaign_id),
        ).fetchone()
        if campaign is None:
            raise EntityNotFound(campaign_id)

        current = self.get(context, campaign_id, for_update=True)
        current_revision = int(current["revision"]) if current is not None else 0
        normalized_expected = 0 if expected_revision is None else int(expected_revision)
        if current_revision != normalized_expected:
            raise CampaignBibleRevisionConflict(
                f"campaign bible {campaign_id} expected revision "
                f"{normalized_expected}; current {current_revision}"
            )

        next_revision = current_revision + 1
        document_value = dict(document)
        provenance_value = dict(provenance or {})
        consistency_value = dict(consistency_report or {})
        completeness_value = dict(completeness or {})
        digest = campaign_bible_hash(document_value)
        encoded = (
            canonical_json(document_value),
            digest,
            canonical_json(provenance_value),
            canonical_json(consistency_value),
            canonical_json(completeness_value),
        )

        row = self.connection.execute(
            f"""
            INSERT INTO omnix_rpg_campaign_bibles (
                workspace_id, campaign_id, revision, document_jsonb, content_hash,
                provenance_jsonb, consistency_report_jsonb, completeness_jsonb
            ) VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s::jsonb)
            ON CONFLICT (workspace_id, campaign_id) DO UPDATE
               SET revision = EXCLUDED.revision,
                   document_jsonb = EXCLUDED.document_jsonb,
                   content_hash = EXCLUDED.content_hash,
                   provenance_jsonb = EXCLUDED.provenance_jsonb,
                   consistency_report_jsonb = EXCLUDED.consistency_report_jsonb,
                   completeness_jsonb = EXCLUDED.completeness_jsonb,
                   updated_at = CURRENT_TIMESTAMP
             WHERE omnix_rpg_campaign_bibles.revision = %s
            RETURNING {_COLUMNS}
            """,
            (
                context.workspace_id,
                campaign_id,
                next_revision,
                *encoded,
                current_revision,
            ),
        ).fetchone()
        if row is None:
            raise CampaignBibleRevisionConflict(
                f"campaign bible changed while writing: {campaign_id}"
            )

        self.connection.execute(
            """
            INSERT INTO omnix_rpg_campaign_bible_revisions (
                workspace_id, campaign_id, revision, document_jsonb, content_hash,
                provenance_jsonb, consistency_report_jsonb, completeness_jsonb
            ) VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s::jsonb)
            """,
            (
                context.workspace_id,
                campaign_id,
                next_revision,
                *encoded,
            ),
        )
        return _row(row)

    def revisions(
        self,
        context: TenantContext,
        campaign_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT workspace_id, campaign_id, revision, document_jsonb,
                   content_hash, provenance_jsonb, consistency_report_jsonb,
                   completeness_jsonb, created_at, created_at
              FROM omnix_rpg_campaign_bible_revisions
             WHERE workspace_id = %s AND campaign_id = %s
             ORDER BY revision DESC
             LIMIT %s
            """,
            (context.workspace_id, campaign_id, max(1, min(int(limit), 500))),
        ).fetchall()
        return [_row(row) for row in rows]
