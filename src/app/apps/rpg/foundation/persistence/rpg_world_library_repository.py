from __future__ import annotations

from typing import Any

from app.persistence.tenant import TenantContext
from app.runtime.pagination import MAX_PAGE_SIZE, page_limit


# RPG is being retired; reads stay bounded until it is removed (WP-5.5).
MAX_ROWS_PER_READ = 10_000

class PostgresRpgWorldLibraryRepository:
    """Read models for the Worlds & Campaigns authoring library."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def list_scenarios(
        self,
        context: TenantContext,
        *,
        world_id: str | None = None,
        limit: int = 200,
        before: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """One page, newest first; pass the last row's ``(updated_at, id)`` as ``before``."""
        clauses = ["workspace_id = %s"]
        params: list[Any] = [context.workspace_id]
        if world_id:
            clauses.append("world_id = %s")
            params.append(world_id)
        if before:
            clauses.append("(updated_at < %s::timestamptz OR (updated_at = %s::timestamptz AND id > %s))")
            params.extend([before[0], before[0], before[1]])
        params.append(page_limit(limit, default=200))
        rows = self.connection.execute(
            "SELECT id, world_id, title, description, status, metadata_jsonb, "
            "created_at, updated_at FROM omnix_rpg_scenarios WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, id LIMIT %s",
            tuple(params),
        ).fetchall()
        return [
            {
                "id": str(row[0]),
                "world_id": str(row[1]),
                "title": str(row[2]),
                "description": str(row[3]),
                "status": str(row[4]),
                "metadata": dict(row[5]),
                "created_at": row[6].isoformat(),
                "updated_at": row[7].isoformat(),
            }
            for row in rows
        ]

    def list_campaign_bindings(
        self,
        context: TenantContext,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT bindings.campaign_id, campaigns.title, campaigns.status,
                   campaigns.revision, campaigns.updated_at, bindings.world_id,
                   bindings.world_revision, bindings.world_release,
                   bindings.scenario_id, bindings.scenario_revision,
                   bindings.binding_jsonb, bindings.created_at
              FROM omnix_rpg_campaign_world_bindings AS bindings
              JOIN omnix_rpg_campaigns AS campaigns
                ON campaigns.workspace_id = bindings.workspace_id
               AND campaigns.id = bindings.campaign_id
             WHERE bindings.workspace_id = %s
             ORDER BY campaigns.updated_at DESC, bindings.campaign_id
             LIMIT %s
            """,
            (context.workspace_id, page_limit(limit, default=200)),
        ).fetchall()
        return [
            {
                "campaign_id": str(row[0]),
                "title": str(row[1]),
                "status": str(row[2]),
                "revision": int(row[3]),
                "updated_at": row[4].isoformat(),
                "world_id": str(row[5]),
                "world_revision": int(row[6]),
                "world_release": int(row[7]),
                "scenario_id": str(row[8]),
                "scenario_revision": int(row[9]),
                "binding": dict(row[10]),
                "created_at": row[11].isoformat(),
            }
            for row in rows
        ]

    def list_world_revisions(
        self,
        context: TenantContext,
        world_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT revision, document_jsonb, content_hash, created_at
              FROM omnix_rpg_world_revisions
             WHERE workspace_id = %s AND world_id = %s
             ORDER BY revision DESC
              LIMIT %s
            """,
            (context.workspace_id, world_id, MAX_ROWS_PER_READ),
        ).fetchall()
        return [
            {
                "revision": int(row[0]),
                "document": dict(row[1]),
                "content_hash": str(row[2]),
                "created_at": row[3].isoformat(),
            }
            for row in rows
        ]

    def list_world_releases(
        self,
        context: TenantContext,
        world_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT world_revision, release, document_jsonb, release_hash, created_at
              FROM omnix_rpg_world_releases
             WHERE workspace_id = %s AND world_id = %s
             ORDER BY world_revision DESC, release DESC
              LIMIT %s
            """,
            (context.workspace_id, world_id, MAX_ROWS_PER_READ),
        ).fetchall()
        return [
            {
                "world_revision": int(row[0]),
                "release": int(row[1]),
                "document": dict(row[2]),
                "release_hash": str(row[3]),
                "created_at": row[4].isoformat(),
            }
            for row in rows
        ]

    def list_scenario_revisions(
        self,
        context: TenantContext,
        scenario_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT revision, world_id, world_revision, document_jsonb,
                   content_hash, created_at
              FROM omnix_rpg_scenario_revisions
             WHERE workspace_id = %s AND scenario_id = %s
             ORDER BY revision DESC
              LIMIT %s
            """,
            (context.workspace_id, scenario_id, MAX_ROWS_PER_READ),
        ).fetchall()
        return [
            {
                "revision": int(row[0]),
                "world_id": str(row[1]),
                "world_revision": int(row[2]),
                "document": dict(row[3]),
                "content_hash": str(row[4]),
                "created_at": row[5].isoformat(),
            }
            for row in rows
        ]

    def list_topics(
        self,
        context: TenantContext,
        world_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT topic_id, draft_revision, source, status, content_jsonb,
                   directives_jsonb, dependency_hashes_jsonb, input_hash,
                   content_hash, provenance_jsonb, updated_at
              FROM omnix_rpg_world_topics
             WHERE workspace_id = %s AND world_id = %s
             ORDER BY topic_id
              LIMIT %s
            """,
            (context.workspace_id, world_id, MAX_ROWS_PER_READ),
        ).fetchall()
        return [
            {
                "topic_id": str(row[0]),
                "draft_revision": int(row[1]),
                "source": str(row[2]),
                "status": str(row[3]),
                "content": dict(row[4]),
                "directives": dict(row[5]),
                "dependency_hashes": dict(row[6]),
                "input_hash": str(row[7]),
                "content_hash": str(row[8]),
                "provenance": dict(row[9]),
                "updated_at": row[10].isoformat(),
            }
            for row in rows
        ]

    def list_generation_runs(
        self,
        context: TenantContext,
        *,
        world_id: str | None = None,
        limit: int = 100,
        before: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """One page, newest first; pass the last row's ``(updated_at, run_id)`` as ``before``."""
        clauses = ["workspace_id = %s"]
        params: list[Any] = [context.workspace_id]
        if world_id:
            clauses.append("world_id = %s")
            params.append(world_id)
        if before:
            clauses.append("(updated_at < %s::timestamptz OR (updated_at = %s::timestamptz AND run_id > %s))")
            params.extend([before[0], before[0], before[1]])
        params.append(page_limit(limit, default=100))
        rows = self.connection.execute(
            "SELECT run_id, world_id, draft_revision, status, graph_jsonb, "
            "context_jsonb, settings_jsonb, plan_jsonb, progress_jsonb, "
            "error_jsonb, parent_run_id, lineage_jsonb, created_at, updated_at, "
            "completed_at FROM omnix_rpg_world_generation_runs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, run_id LIMIT %s",
            tuple(params),
        ).fetchall()
        return [
            {
                "run_id": str(row[0]),
                "world_id": str(row[1]),
                "draft_revision": int(row[2]),
                "status": str(row[3]),
                "graph": dict(row[4]),
                "context": dict(row[5]),
                "settings": dict(row[6]),
                "plan": dict(row[7]),
                "progress": dict(row[8]),
                "error": dict(row[9]),
                "parent_run_id": str(row[10]) if row[10] is not None else None,
                "lineage": dict(row[11]),
                "created_at": row[12].isoformat(),
                "updated_at": row[13].isoformat(),
                "completed_at": row[14].isoformat() if row[14] is not None else None,
            }
            for row in rows
        ]

    def iter_scenarios(self, context: TenantContext, *, world_id: str | None = None):
        """Every scenario (of one world), newest first, a page at a time."""
        before: tuple[str, str] | None = None
        while True:
            page = self.list_scenarios(context, world_id=world_id, limit=MAX_PAGE_SIZE, before=before)
            yield from page
            if len(page) < MAX_PAGE_SIZE:
                return
            before = (page[-1]["updated_at"], page[-1]["id"])

    def iter_generation_runs(self, context: TenantContext, *, world_id: str | None = None):
        """Every generation run (of one world), newest first, a page at a time."""
        before: tuple[str, str] | None = None
        while True:
            page = self.list_generation_runs(context, world_id=world_id, limit=MAX_PAGE_SIZE, before=before)
            yield from page
            if len(page) < MAX_PAGE_SIZE:
                return
            before = (page[-1]["updated_at"], page[-1]["run_id"])

    def published_scenario_counts(self, context: TenantContext, world_ids: list[str]) -> dict[str, int]:
        if not world_ids:
            return {}
        rows = self.connection.execute(
            "SELECT world_id, count(*) FROM omnix_rpg_scenarios "
            "WHERE workspace_id = %s AND world_id = ANY(%s) AND lower(status) = 'published' "
            "GROUP BY world_id LIMIT %s",
            (context.workspace_id, list(world_ids), len(world_ids)),
        ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def latest_generation_runs(self, context: TenantContext, world_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Each world's most recently updated generation run."""
        latest: dict[str, dict[str, Any]] = {}
        for world_id in world_ids:
            page = self.list_generation_runs(context, world_id=world_id, limit=1)
            if page:
                latest[world_id] = page[0]
        return latest
