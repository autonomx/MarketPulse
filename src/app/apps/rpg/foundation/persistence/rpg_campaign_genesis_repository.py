from __future__ import annotations

from typing import Any, Mapping

from app.persistence.errors import EntityNotFound
from .rpg_repository import canonical_json
from app.persistence.tenant import TenantContext


_COLUMNS = """
workspace_id, genesis_run_id, campaign_id, status, depth,
topic_graph_jsonb, jobs_jsonb, progress_jsonb, audit_jsonb,
bible_revision, bible_content_hash, error_jsonb,
created_at, updated_at, completed_at
"""


def _row(value: Any) -> dict[str, Any]:
    return {
        "workspace_id": str(value[0]),
        "genesis_run_id": str(value[1]),
        "campaign_id": str(value[2]),
        "status": str(value[3]),
        "depth": str(value[4]),
        "topic_graph": dict(value[5]),
        "jobs": list(value[6]),
        "progress": dict(value[7]),
        "audit": dict(value[8]),
        "bible_revision": int(value[9]) if value[9] is not None else None,
        "bible_content_hash": str(value[10]),
        "error": dict(value[11]),
        "created_at": value[12].isoformat(),
        "updated_at": value[13].isoformat(),
        "completed_at": value[14].isoformat() if value[14] is not None else None,
    }


class PostgresRpgCampaignGenesisRepository:
    """Transactional progress and launch-gate state for Campaign Genesis."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def start(
        self,
        context: TenantContext,
        *,
        genesis_run_id: str,
        campaign_id: str,
        depth: str,
        topic_graph: Mapping[str, Any],
        progress: Mapping[str, Any],
    ) -> dict[str, Any]:
        campaign = self.connection.execute(
            "SELECT id FROM omnix_rpg_campaigns WHERE workspace_id = %s AND id = %s",
            (context.workspace_id, campaign_id),
        ).fetchone()
        if campaign is None:
            raise EntityNotFound(campaign_id)
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_rpg_campaign_genesis_runs (
                workspace_id, genesis_run_id, campaign_id, status, depth,
                topic_graph_jsonb, progress_jsonb
            ) VALUES (%s, %s, %s, 'planned', %s, %s::jsonb, %s::jsonb)
            ON CONFLICT (workspace_id, campaign_id) DO UPDATE
               SET genesis_run_id = EXCLUDED.genesis_run_id,
                   status = EXCLUDED.status,
                   depth = EXCLUDED.depth,
                   topic_graph_jsonb = EXCLUDED.topic_graph_jsonb,
                   jobs_jsonb = '[]'::jsonb,
                   progress_jsonb = EXCLUDED.progress_jsonb,
                   audit_jsonb = '{{}}'::jsonb,
                   bible_revision = NULL,
                   bible_content_hash = '',
                   error_jsonb = '{{}}'::jsonb,
                   completed_at = NULL,
                   updated_at = CURRENT_TIMESTAMP
            RETURNING {_COLUMNS}
            """,
            (
                context.workspace_id,
                genesis_run_id,
                campaign_id,
                depth,
                canonical_json(dict(topic_graph)),
                canonical_json(dict(progress)),
            ),
        ).fetchone()
        return _row(row)

    def update(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        status: str,
        jobs: list[Mapping[str, Any]] | None = None,
        progress: Mapping[str, Any] | None = None,
        audit: Mapping[str, Any] | None = None,
        bible_revision: int | None = None,
        bible_content_hash: str | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            UPDATE omnix_rpg_campaign_genesis_runs
               SET status = %s,
                   jobs_jsonb = COALESCE(%s::jsonb, jobs_jsonb),
                   progress_jsonb = COALESCE(%s::jsonb, progress_jsonb),
                   audit_jsonb = COALESCE(%s::jsonb, audit_jsonb),
                   bible_revision = COALESCE(%s, bible_revision),
                   bible_content_hash = COALESCE(%s, bible_content_hash),
                   error_jsonb = COALESCE(%s::jsonb, error_jsonb),
                   completed_at = CASE WHEN %s IN ('ready', 'failed') THEN CURRENT_TIMESTAMP ELSE completed_at END,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND campaign_id = %s
            RETURNING {_COLUMNS}
            """,
            (
                status,
                canonical_json([dict(row) for row in jobs]) if jobs is not None else None,
                canonical_json(dict(progress)) if progress is not None else None,
                canonical_json(dict(audit)) if audit is not None else None,
                bible_revision,
                bible_content_hash,
                canonical_json(dict(error)) if error is not None else None,
                status,
                context.workspace_id,
                campaign_id,
            ),
        ).fetchone()
        if row is None:
            raise EntityNotFound(campaign_id)
        return _row(row)

    def get(self, context: TenantContext, campaign_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {_COLUMNS} FROM omnix_rpg_campaign_genesis_runs "
            "WHERE workspace_id = %s AND campaign_id = %s",
            (context.workspace_id, campaign_id),
        ).fetchone()
        return _row(row) if row is not None else None
