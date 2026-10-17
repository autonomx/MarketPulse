from __future__ import annotations

from typing import Any, Mapping

from app.persistence.errors import EntityNotFound, RevisionConflict
from .rpg_repository import canonical_json
from app.persistence.tenant import TenantContext


def _row(value: Any) -> dict[str, Any]:
    return {
        "workspace_id": str(value[0]),
        "research_id": str(value[1]),
        "campaign_id": str(value[2]),
        "request": dict(value[3]),
        "result": dict(value[4]),
        "status": str(value[5]),
        "source_count": int(value[6]),
        "finding_count": int(value[7]),
        "content_hash": str(value[8]),
        "created_at": value[9].isoformat(),
    }


_COLUMNS = """
workspace_id, research_id, campaign_id, request_jsonb, result_jsonb, status,
source_count, finding_count, content_hash, created_at
"""


class PostgresRpgHermesResearchRepository:
    """Append-only persistence for bounded read-only Hermes research."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create(
        self,
        context: TenantContext,
        *,
        research_id: str,
        campaign_id: str,
        request: Mapping[str, Any],
        result: Mapping[str, Any],
        status: str = "complete",
    ) -> dict[str, Any]:
        campaign = self.connection.execute(
            "SELECT id FROM omnix_rpg_campaigns WHERE workspace_id = %s AND id = %s",
            (context.workspace_id, campaign_id),
        ).fetchone()
        if campaign is None:
            raise EntityNotFound(campaign_id)
        sources = raw_sources if isinstance(raw_sources := result.get("sources"), list) else []
        findings = raw_findings if isinstance(raw_findings := result.get("findings"), list) else []
        content_hash = str(result.get("content_hash") or "").strip()
        if not content_hash:
            raise ValueError("Hermes research result requires a content_hash")
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_rpg_hermes_research (
                workspace_id, research_id, campaign_id, request_jsonb,
                result_jsonb, status, source_count, finding_count, content_hash
            ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING {_COLUMNS}
            """,
            (
                context.workspace_id,
                research_id,
                campaign_id,
                canonical_json(dict(request)),
                canonical_json(dict(result)),
                status,
                len(sources),
                len(findings),
                content_hash,
            ),
        ).fetchone()
        if row is None:
            existing = self.get(context, research_id)
            if existing is not None and existing["content_hash"] == content_hash:
                return existing
            raise RevisionConflict(f"Hermes research id already exists: {research_id}")
        return _row(row)

    def get(self, context: TenantContext, research_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {_COLUMNS} FROM omnix_rpg_hermes_research "
            "WHERE workspace_id = %s AND research_id = %s",
            (context.workspace_id, research_id),
        ).fetchone()
        return _row(row) if row is not None else None

