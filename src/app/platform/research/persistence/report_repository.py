from __future__ import annotations

import json
from typing import Any

from app.persistence.errors import EntityNotFound
from app.persistence.tenant import TenantContext


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class PostgresResearchReportRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def put_research(
        self,
        context: TenantContext,
        *,
        record_id: str,
        research_type: str,
        query_text: str | None,
        result: dict[str, Any],
        source_fingerprint: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            INSERT INTO omnix_research_records (
                id, workspace_id, owner_user_id, research_type, query_text,
                result_jsonb, source_fingerprint, expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::timestamptz)
            ON CONFLICT (id) DO UPDATE SET
                result_jsonb = EXCLUDED.result_jsonb,
                source_fingerprint = EXCLUDED.source_fingerprint,
                expires_at = EXCLUDED.expires_at,
                revision = omnix_research_records.revision + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE omnix_research_records.workspace_id = EXCLUDED.workspace_id
            RETURNING id, research_type, query_text, result_jsonb,
                      source_fingerprint, status, expires_at, revision,
                      created_at, updated_at
            """,
            (
                record_id,
                context.workspace_id,
                context.user_id,
                research_type,
                query_text,
                _json(result),
                source_fingerprint,
                expires_at,
            ),
        ).fetchone()
        if row is None:
            raise EntityNotFound(record_id)
        return {
            "id": str(row[0]),
            "research_type": str(row[1]),
            "query_text": str(row[2]) if row[2] is not None else None,
            "result": dict(row[3]),
            "source_fingerprint": str(row[4]) if row[4] is not None else None,
            "status": str(row[5]),
            "expires_at": row[6].isoformat() if row[6] is not None else None,
            "revision": int(row[7]),
            "created_at": row[8].isoformat(),
            "updated_at": row[9].isoformat(),
        }

    def create_report(
        self,
        context: TenantContext,
        *,
        report_id: str,
        report_type: str,
        title: str,
        summary: dict[str, Any],
        blob_asset_id: str | None = None,
        generated_by_job_id: str | None = None,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            INSERT INTO omnix_reports (
                id, workspace_id, owner_user_id, report_type, title,
                summary, blob_asset_id, generated_by_job_id
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            RETURNING id, report_type, title, status, summary, blob_asset_id,
                      generated_by_job_id, revision, created_at, updated_at
            """,
            (
                report_id,
                context.workspace_id,
                context.user_id,
                report_type,
                title,
                _json(summary),
                blob_asset_id,
                generated_by_job_id,
            ),
        ).fetchone()
        return {
            "id": str(row[0]),
            "report_type": str(row[1]),
            "title": str(row[2]),
            "status": str(row[3]),
            "summary": dict(row[4]),
            "blob_asset_id": str(row[5]) if row[5] is not None else None,
            "generated_by_job_id": str(row[6]) if row[6] is not None else None,
            "revision": int(row[7]),
            "created_at": row[8].isoformat(),
            "updated_at": row[9].isoformat(),
        }
