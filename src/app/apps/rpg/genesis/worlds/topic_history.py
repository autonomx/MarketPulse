"""Review and restore preserved reusable-world topic draft history."""
from __future__ import annotations

from typing import Any

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from .lifecycle_service import require_world_writable


def _history_row(row: Any) -> dict[str, Any]:
    return {
        "history_sequence": int(row[0]),
        "topic_id": str(row[1]),
        "draft_revision": int(row[2]),
        "source": str(row[3]),
        "status": str(row[4]),
        "content": dict(row[5]),
        "directives": dict(row[6]),
        "dependency_hashes": dict(row[7]),
        "input_hash": str(row[8]),
        "content_hash": str(row[9]),
        "provenance": dict(row[10]),
        "topic_updated_at": row[11].isoformat(),
        "captured_at": row[12].isoformat(),
    }


def list_world_topic_history(
    world_id: str,
    *,
    draft_revision: int | None = None,
    latest_per_topic: bool = False,
    database: Any | None = None,
) -> list[dict[str, Any]]:
    context = current_tenant()
    with unit_of_work(database) as work:
        if work.world_scenarios.get_world(context, world_id) is None:
            raise KeyError(f"world_not_found:{world_id}")
        revision_filter = " AND draft_revision = %s" if draft_revision is not None else ""
        params: list[Any] = [context.workspace_id, world_id]
        if draft_revision is not None:
            params.append(int(draft_revision))
        if latest_per_topic:
            sql = (
                "SELECT DISTINCT ON (draft_revision, topic_id) history_sequence, "
                "topic_id, draft_revision, source, status, content_jsonb, "
                "directives_jsonb, dependency_hashes_jsonb, input_hash, "
                "content_hash, provenance_jsonb, topic_updated_at, captured_at "
                "FROM omnix_rpg_world_topic_history WHERE workspace_id = %s "
                "AND world_id = %s"
                + revision_filter
                + " ORDER BY draft_revision DESC, topic_id, history_sequence DESC"
            )
        else:
            sql = (
                "SELECT history_sequence, topic_id, draft_revision, source, status, "
                "content_jsonb, directives_jsonb, dependency_hashes_jsonb, "
                "input_hash, content_hash, provenance_jsonb, topic_updated_at, "
                "captured_at FROM omnix_rpg_world_topic_history "
                "WHERE workspace_id = %s AND world_id = %s"
                + revision_filter
                + " ORDER BY draft_revision DESC, topic_id, history_sequence DESC"
            )
        rows = work.connection.execute(sql, tuple(params)).fetchall()
        work.rollback()
    return [_history_row(row) for row in rows]


def restore_world_topic_draft(
    world_id: str,
    *,
    source_draft_revision: int,
    expected_current_draft_revision: int,
    database: Any | None = None,
) -> dict[str, Any]:
    """Copy one historical draft into a new current draft revision."""

    context = current_tenant()
    with unit_of_work(database) as work:
        world = require_world_writable(work, context, world_id)
        current_revision = int(world["draft_revision"])
        if current_revision != int(expected_current_draft_revision):
            raise ValueError(
                "world_draft_revision_conflict:"
                f"expected={expected_current_draft_revision}:current={current_revision}"
            )
        active = work.connection.execute(
            "SELECT run_id FROM omnix_rpg_world_generation_runs "
            "WHERE workspace_id = %s AND world_id = %s AND draft_revision = %s "
            "AND status IN ('planned', 'running') ORDER BY created_at LIMIT 1",
            (context.workspace_id, world_id, current_revision),
        ).fetchone()
        if active is not None:
            raise ValueError(f"world_generation_active:{world_id}:{active[0]}")
        rows = work.connection.execute(
            "SELECT DISTINCT ON (topic_id) history_sequence, topic_id, "
            "draft_revision, source, status, content_jsonb, directives_jsonb, "
            "dependency_hashes_jsonb, input_hash, content_hash, provenance_jsonb, "
            "topic_updated_at, captured_at "
            "FROM omnix_rpg_world_topic_history WHERE workspace_id = %s "
            "AND world_id = %s AND draft_revision = %s "
            "ORDER BY topic_id, history_sequence DESC",
            (context.workspace_id, world_id, int(source_draft_revision)),
        ).fetchall()
        if not rows:
            raise KeyError(
                f"world_topic_draft_not_found:{world_id}:{source_draft_revision}"
            )
        source_topics = [_history_row(row) for row in rows]
        target_revision = current_revision + 1
        updated = work.connection.execute(
            "UPDATE omnix_rpg_worlds SET draft_revision = %s, "
            "updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s AND id = %s "
            "AND draft_revision = %s RETURNING draft_revision",
            (
                target_revision,
                context.workspace_id,
                world_id,
                current_revision,
            ),
        ).fetchone()
        if updated is None:
            raise ValueError("world_draft_compare_and_swap_failed")
        restored_topics: list[dict[str, Any]] = []
        for topic in source_topics:
            provenance = {
                **dict(topic["provenance"]),
                "draft_restore": {
                    "source_draft_revision": int(source_draft_revision),
                    "source_history_sequence": int(topic["history_sequence"]),
                    "target_draft_revision": target_revision,
                },
            }
            restored_topics.append(
                work.world_scenarios.put_topic(
                    context,
                    world_id=world_id,
                    topic_id=str(topic["topic_id"]),
                    draft_revision=target_revision,
                    source=str(topic["source"]),
                    status=str(topic["status"]),
                    content=dict(topic["content"]),
                    directives=dict(topic["directives"]),
                    dependency_hashes=dict(topic["dependency_hashes"]),
                    input_hash=str(topic["input_hash"]),
                    content_hash=str(topic["content_hash"]),
                    provenance=provenance,
                )
            )
        work.commit()
    return {
        "ok": True,
        "world_id": world_id,
        "source_draft_revision": int(source_draft_revision),
        "previous_current_draft_revision": current_revision,
        "restored_draft_revision": target_revision,
        "topics": restored_topics,
    }
