from __future__ import annotations

from typing import Any, Mapping

from app.persistence.errors import EntityNotFound
from .rpg_repository import canonical_json
from app.persistence.tenant import TenantContext


_RUN_COLUMNS = """
workspace_id, run_id, world_id, draft_revision, status, graph_jsonb,
context_jsonb, settings_jsonb, plan_jsonb, progress_jsonb, error_jsonb,
parent_run_id, lineage_jsonb, created_at, updated_at, completed_at
"""

_RESULT_COLUMNS = """
workspace_id, run_id, world_id, draft_revision, topic_id, status,
candidate_jsonb, candidate_hash, validation_jsonb, provider_jsonb,
dependency_hashes_jsonb, dependency_trust_jsonb, job_id,
created_at, updated_at
"""

_PERSISTENT_PROGRESS_KEYS = {
    "stale_topic_ids",
    "stale_topics",
}


# RPG is being retired; reads stay bounded until it is removed (WP-5.5).
MAX_ROWS_PER_READ = 10_000

def _run_row(row: Any) -> dict[str, Any]:
    return {
        "workspace_id": str(row[0]),
        "run_id": str(row[1]),
        "world_id": str(row[2]),
        "draft_revision": int(row[3]),
        "status": str(row[4]),
        "graph": dict(row[5]),
        "context": dict(row[6]),
        "settings": dict(row[7]),
        "plan": dict(row[8]),
        "progress": dict(row[9]),
        "error": dict(row[10]),
        "parent_run_id": str(row[11]) if row[11] is not None else None,
        "lineage": dict(row[12]),
        "created_at": row[13].isoformat(),
        "updated_at": row[14].isoformat(),
        "completed_at": row[15].isoformat() if row[15] is not None else None,
    }


def _result_row(row: Any) -> dict[str, Any]:
    return {
        "workspace_id": str(row[0]),
        "run_id": str(row[1]),
        "world_id": str(row[2]),
        "draft_revision": int(row[3]),
        "topic_id": str(row[4]),
        "status": str(row[5]),
        "candidate": dict(row[6]) if row[6] is not None else None,
        "candidate_hash": str(row[7]),
        "validation": dict(row[8]),
        "provider": dict(row[9]),
        "dependency_hashes": dict(row[10]),
        "dependency_trust": dict(row[11]),
        "job_id": str(row[12]),
        "created_at": row[13].isoformat(),
        "updated_at": row[14].isoformat(),
    }


def _merge_persistent_progress(
    current: Mapping[str, Any] | None,
    replacement: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep unresolved staleness unless a caller explicitly clears it."""

    merged = dict(replacement)
    prior = dict(current or {})
    for key in _PERSISTENT_PROGRESS_KEYS:
        if key not in merged and key in prior:
            merged[key] = prior[key]
    if merged.get("stale_topic_ids"):
        merged["publication_blocked"] = True
    return merged


class PostgresRpgWorldGenerationRepository:
    """Durable coordination state and immutable topic results for World Forge DAGs."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def start(
        self,
        context: TenantContext,
        *,
        run_id: str,
        world_id: str,
        draft_revision: int,
        graph: Mapping[str, Any],
        generation_context: Mapping[str, Any],
        settings: Mapping[str, Any],
        plan: Mapping[str, Any],
        progress: Mapping[str, Any],
    ) -> dict[str, Any]:
        world = self.connection.execute(
            "SELECT status FROM omnix_rpg_worlds WHERE workspace_id = %s "
            "AND id = %s FOR UPDATE",
            (context.workspace_id, world_id),
        ).fetchone()
        if world is None:
            raise EntityNotFound(world_id)
        if str(world[0]) == "archived":
            raise ValueError(f"world_archived:{world_id}")
        parent = self.connection.execute(
            "SELECT run_id, draft_revision, lineage_jsonb "
            "FROM omnix_rpg_world_generation_runs "
            "WHERE workspace_id = %s AND world_id = %s AND run_id <> %s "
            "ORDER BY created_at DESC LIMIT 1",
            (context.workspace_id, world_id, run_id),
        ).fetchone()
        parent_run_id = str(parent[0]) if parent is not None else None
        parent_draft_revision = int(parent[1]) if parent is not None else None
        parent_lineage = dict(parent[2]) if parent is not None else {}
        root_run_id = str(parent_lineage.get("root_run_id") or parent_run_id or run_id)
        lineage = {
            "root_run_id": root_run_id,
            "parent_run_id": parent_run_id,
            "parent_draft_revision": parent_draft_revision,
            "draft_revision": int(draft_revision),
        }
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_rpg_world_generation_runs (
                workspace_id, run_id, world_id, draft_revision, status,
                graph_jsonb, context_jsonb, settings_jsonb, plan_jsonb,
                progress_jsonb, parent_run_id, lineage_jsonb
            ) VALUES (%s, %s, %s, %s, 'planned', %s::jsonb, %s::jsonb,
                      %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb)
            ON CONFLICT (workspace_id, run_id) DO NOTHING
            RETURNING {_RUN_COLUMNS}
            """,
            (
                context.workspace_id,
                run_id,
                world_id,
                int(draft_revision),
                canonical_json(dict(graph)),
                canonical_json(dict(generation_context)),
                canonical_json(dict(settings)),
                canonical_json(dict(plan)),
                canonical_json(dict(progress)),
                parent_run_id,
                canonical_json(lineage),
            ),
        ).fetchone()
        if row is not None:
            return _run_row(row)
        existing = self.get(context, run_id)
        if existing is None:
            raise RuntimeError("world_generation_run_insert_failed")
        return existing

    def get(self, context: TenantContext, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {_RUN_COLUMNS} FROM omnix_rpg_world_generation_runs "
            "WHERE workspace_id = %s AND run_id = %s",
            (context.workspace_id, run_id),
        ).fetchone()
        return _run_row(row) if row is not None else None

    def update(
        self,
        context: TenantContext,
        *,
        run_id: str,
        status: str | None = None,
        plan: Mapping[str, Any] | None = None,
        progress: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_progress: dict[str, Any] | None = None
        if progress is not None:
            current = self.get(context, run_id)
            if current is None:
                raise EntityNotFound(run_id)
            resolved_progress = _merge_persistent_progress(
                current.get("progress"),
                progress,
            )
        row = self.connection.execute(
            f"""
            UPDATE omnix_rpg_world_generation_runs
               SET status = COALESCE(%s, status),
                   plan_jsonb = COALESCE(%s::jsonb, plan_jsonb),
                   progress_jsonb = COALESCE(%s::jsonb, progress_jsonb),
                   error_jsonb = COALESCE(%s::jsonb, error_jsonb),
                   completed_at = CASE
                       WHEN COALESCE(%s, status) IN ('review', 'ready', 'failed', 'canceled')
                       THEN COALESCE(completed_at, CURRENT_TIMESTAMP)
                       ELSE NULL
                   END,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s
            RETURNING {_RUN_COLUMNS}
            """,
            (
                status,
                canonical_json(dict(plan)) if plan is not None else None,
                canonical_json(resolved_progress) if resolved_progress is not None else None,
                canonical_json(dict(error)) if error is not None else None,
                status,
                context.workspace_id,
                run_id,
            ),
        ).fetchone()
        if row is None:
            raise EntityNotFound(run_id)
        return _run_row(row)

    def put_topic_result(
        self,
        context: TenantContext,
        *,
        run_id: str,
        world_id: str,
        draft_revision: int,
        topic_id: str,
        status: str,
        candidate: Mapping[str, Any] | None,
        candidate_hash: str,
        validation: Mapping[str, Any],
        provider: Mapping[str, Any],
        dependency_hashes: Mapping[str, Any],
        dependency_trust: Mapping[str, Any],
        job_id: str = "",
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_rpg_world_generation_topic_results (
                workspace_id, run_id, world_id, draft_revision, topic_id, status,
                candidate_jsonb, candidate_hash, validation_jsonb, provider_jsonb,
                dependency_hashes_jsonb, dependency_trust_jsonb, job_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb,
                %s::jsonb, %s::jsonb, %s
            )
            ON CONFLICT (workspace_id, run_id, topic_id) DO NOTHING
            RETURNING {_RESULT_COLUMNS}
            """,
            (
                context.workspace_id,
                run_id,
                world_id,
                int(draft_revision),
                topic_id,
                status,
                canonical_json(dict(candidate)) if candidate is not None else None,
                candidate_hash,
                canonical_json(dict(validation)),
                canonical_json(dict(provider)),
                canonical_json(dict(dependency_hashes)),
                canonical_json(dict(dependency_trust)),
                job_id,
            ),
        ).fetchone()
        if row is not None:
            return _result_row(row)
        existing = self.get_topic_result(context, run_id=run_id, topic_id=topic_id)
        if existing is None:
            raise RuntimeError("world_generation_topic_result_insert_failed")
        same = (
            existing["status"] == status
            and existing["candidate_hash"] == candidate_hash
            and existing["validation"] == dict(validation)
            and existing["dependency_hashes"] == dict(dependency_hashes)
            and existing["dependency_trust"] == dict(dependency_trust)
        )
        if not same:
            raise RuntimeError(
                f"world_generation_topic_result_conflict:{run_id}:{topic_id}"
            )
        return existing

    def get_topic_result(
        self,
        context: TenantContext,
        *,
        run_id: str,
        topic_id: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {_RESULT_COLUMNS} "
            "FROM omnix_rpg_world_generation_topic_results "
            "WHERE workspace_id = %s AND run_id = %s AND topic_id = %s",
            (context.workspace_id, run_id, topic_id),
        ).fetchone()
        return _result_row(row) if row is not None else None

    def list_topic_results(
        self,
        context: TenantContext,
        *,
        run_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            f"SELECT {_RESULT_COLUMNS} "
            "FROM omnix_rpg_world_generation_topic_results "
            "WHERE workspace_id = %s AND run_id = %s ORDER BY topic_id LIMIT %s",
            (context.workspace_id, run_id, MAX_ROWS_PER_READ),
        ).fetchall()
        return [_result_row(row) for row in rows]

    def get_topic(
        self,
        context: TenantContext,
        *,
        world_id: str,
        topic_id: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT topic_id, draft_revision, source, status, content_jsonb,
                   directives_jsonb, dependency_hashes_jsonb, input_hash,
                   content_hash, provenance_jsonb, updated_at
              FROM omnix_rpg_world_topics
             WHERE workspace_id = %s AND world_id = %s AND topic_id = %s
            """,
            (context.workspace_id, world_id, topic_id),
        ).fetchone()
        return _topic_row(row) if row is not None else None

    def list_topics(
        self,
        context: TenantContext,
        *,
        world_id: str,
        draft_revision: int,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT topic_id, draft_revision, source, status, content_jsonb,
                   directives_jsonb, dependency_hashes_jsonb, input_hash,
                   content_hash, provenance_jsonb, updated_at
              FROM omnix_rpg_world_topics
             WHERE workspace_id = %s AND world_id = %s AND draft_revision = %s
             ORDER BY topic_id
              LIMIT %s
            """,
            (context.workspace_id, world_id, int(draft_revision), MAX_ROWS_PER_READ),
        ).fetchall()
        return [_topic_row(row) for row in rows]


def _topic_row(row: Any) -> dict[str, Any]:
    return {
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
