"""Concurrency-safe topic editing, locking, stale propagation, and restore."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.forge.world_forge_contract import build_campaign_topic_graph

from .generation_jobs import canonical_hash
from .lifecycle_service import require_world_writable


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _authoring(row: Mapping[str, Any] | None) -> dict[str, Any]:
    return _record(_record((row or {}).get("provenance")).get("authoring"))


def _graph_nodes(world: Mapping[str, Any], run: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    graph = _record((run or {}).get("graph"))
    nodes = [dict(row) for row in graph.get("nodes") or [] if isinstance(row, Mapping)]
    if nodes:
        return nodes
    metadata = _record(world.get("metadata"))
    generated = build_campaign_topic_graph(
        campaign_template=str(metadata.get("campaign_template") or "classic_fantasy"),
        genre=str(world.get("genre") or "classic_fantasy"),
        tone=str(world.get("tone") or "heroic adventure"),
        depth="standard",
        background_expansion=True,
    )
    return [node.as_dict() for node in generated.nodes]


def _downstream_topic_ids(nodes: list[dict[str, Any]], topic_id: str) -> list[str]:
    reverse: dict[str, set[str]] = {}
    for node in nodes:
        candidate = str(node.get("topic_id") or "")
        for dependency in node.get("dependencies") or []:
            reverse.setdefault(str(dependency), set()).add(candidate)
    discovered: set[str] = set()
    pending = list(reverse.get(topic_id, ()))
    while pending:
        candidate = pending.pop(0)
        if not candidate or candidate in discovered:
            continue
        discovered.add(candidate)
        pending.extend(sorted(reverse.get(candidate, ())))
    return sorted(discovered)


def _topic_history(work: Any, context: Any, world_id: str, topic_id: str) -> list[dict[str, Any]]:
    rows = work.connection.execute(
        "SELECT history_sequence, topic_id, draft_revision, source, status, "
        "content_jsonb, directives_jsonb, dependency_hashes_jsonb, input_hash, "
        "content_hash, provenance_jsonb, topic_updated_at, captured_at "
        "FROM omnix_rpg_world_topic_history WHERE workspace_id = %s "
        "AND world_id = %s AND topic_id = %s ORDER BY history_sequence DESC",
        (context.workspace_id, world_id, topic_id),
    ).fetchall()
    return [
        {
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
        for row in rows
    ]


def read_world_topic(
    world_id: str,
    topic_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world = work.world_scenarios.get_world(context, world_id)
        if world is None:
            raise KeyError(f"world_not_found:{world_id}")
        topic = work.world_generation.get_topic(
            context,
            world_id=world_id,
            topic_id=topic_id,
        )
        if topic is None:
            raise KeyError(f"world_topic_not_found:{world_id}:{topic_id}")
        history = _topic_history(work, context, world_id, topic_id)
        work.rollback()
    return {"ok": True, "world": world, "topic": topic, "history": history}


def _latest_run(work: Any, context: Any, world_id: str) -> dict[str, Any] | None:
    runs = work.world_library.list_generation_runs(context, world_id=world_id, limit=1)
    return runs[0] if runs else None


def _assert_writable_topic(
    work: Any,
    context: Any,
    *,
    world_id: str,
    topic_id: str,
    expected_draft_revision: int,
    expected_content_hash: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    world = require_world_writable(work, context, world_id)
    current_revision = int(world["draft_revision"])
    if current_revision != int(expected_draft_revision):
        raise ValueError(
            "world_draft_revision_conflict:"
            f"expected={expected_draft_revision}:current={current_revision}"
        )
    active = work.connection.execute(
        "SELECT run_id FROM omnix_rpg_world_generation_runs WHERE workspace_id = %s "
        "AND world_id = %s AND draft_revision = %s AND status IN ('planned', 'running') "
        "ORDER BY created_at LIMIT 1",
        (context.workspace_id, world_id, current_revision),
    ).fetchone()
    if active is not None:
        raise ValueError(f"world_generation_active:{world_id}:{active[0]}")
    topic = work.world_generation.get_topic(
        context,
        world_id=world_id,
        topic_id=topic_id,
    )
    if topic is None:
        raise KeyError(f"world_topic_not_found:{world_id}:{topic_id}")
    if str(topic.get("content_hash") or "") != str(expected_content_hash or ""):
        raise ValueError(
            "world_topic_content_hash_conflict:"
            f"expected={expected_content_hash}:current={topic.get('content_hash') or ''}"
        )
    return world, topic


def _mark_downstream_stale(
    work: Any,
    context: Any,
    *,
    world: Mapping[str, Any],
    changed_topic_id: str,
    changed_content_hash: str,
) -> list[str]:
    run = _latest_run(work, context, str(world["id"]))
    stale_ids: list[str] = []
    for dependent_id in _downstream_topic_ids(_graph_nodes(world, run), changed_topic_id):
        dependent = work.world_generation.get_topic(
            context,
            world_id=str(world["id"]),
            topic_id=dependent_id,
        )
        if dependent is None or bool(_authoring(dependent).get("generation_lock")):
            continue
        provenance = _record(dependent.get("provenance"))
        provenance["authoring"] = {
            **_authoring(dependent),
            "stale_reason": {
                "dependency_topic_id": changed_topic_id,
                "dependency_content_hash": changed_content_hash,
            },
        }
        work.world_scenarios.put_topic(
            context,
            world_id=str(world["id"]),
            topic_id=dependent_id,
            draft_revision=int(world["draft_revision"]),
            source=str(dependent.get("source") or "ai"),
            status="stale",
            content=_record(dependent.get("content")),
            directives=_record(dependent.get("directives")),
            dependency_hashes=_record(dependent.get("dependency_hashes")),
            input_hash=str(dependent.get("input_hash") or ""),
            content_hash=str(dependent.get("content_hash") or ""),
            provenance=provenance,
        )
        stale_ids.append(dependent_id)
    return stale_ids


def update_world_topic(
    world_id: str,
    topic_id: str,
    *,
    expected_draft_revision: int,
    expected_content_hash: str,
    content: Mapping[str, Any],
    generation_lock: bool = True,
    approved: bool = False,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world, current = _assert_writable_topic(
            work,
            context,
            world_id=world_id,
            topic_id=topic_id,
            expected_draft_revision=expected_draft_revision,
            expected_content_hash=expected_content_hash,
        )
        payload = dict(content)
        payload.setdefault("topic_id", topic_id)
        content_hash = canonical_hash(payload)
        provenance = _record(current.get("provenance"))
        authoring = {
            **_authoring(current),
            "edit_state": "manually_edited",
            "generation_lock": bool(generation_lock),
            "edited_at": datetime.now(timezone.utc).isoformat(),
        }
        if approved:
            authoring["approved_at"] = datetime.now(timezone.utc).isoformat()
        provenance["authoring"] = authoring
        stored = work.world_scenarios.put_topic(
            context,
            world_id=world_id,
            topic_id=topic_id,
            draft_revision=int(world["draft_revision"]),
            source="manual",
            status="ready",
            content=payload,
            directives=_record(current.get("directives")),
            dependency_hashes=_record(current.get("dependency_hashes")),
            input_hash=canonical_hash({"source": "manual", "content": payload}),
            content_hash=content_hash,
            provenance=provenance,
        )
        stale_ids = _mark_downstream_stale(
            work,
            context,
            world=world,
            changed_topic_id=topic_id,
            changed_content_hash=content_hash,
        )
        work.commit()
    return {"ok": True, "topic": stored, "stale_topic_ids": stale_ids}


def restore_world_topic(
    world_id: str,
    topic_id: str,
    *,
    history_sequence: int,
    expected_draft_revision: int,
    expected_content_hash: str,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world, current = _assert_writable_topic(
            work,
            context,
            world_id=world_id,
            topic_id=topic_id,
            expected_draft_revision=expected_draft_revision,
            expected_content_hash=expected_content_hash,
        )
        row = work.connection.execute(
            "SELECT source, status, content_jsonb, directives_jsonb, "
            "dependency_hashes_jsonb, input_hash, content_hash, provenance_jsonb "
            "FROM omnix_rpg_world_topic_history WHERE workspace_id = %s "
            "AND world_id = %s AND topic_id = %s AND history_sequence = %s",
            (context.workspace_id, world_id, topic_id, int(history_sequence)),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"world_topic_history_not_found:{world_id}:{topic_id}:{history_sequence}"
            )
        provenance = dict(row[7])
        provenance["authoring"] = {
            **_record(provenance.get("authoring")),
            "edit_state": "manually_edited",
            "generation_lock": True,
            "restored_from_history_sequence": int(history_sequence),
            "restored_at": datetime.now(timezone.utc).isoformat(),
        }
        stored = work.world_scenarios.put_topic(
            context,
            world_id=world_id,
            topic_id=topic_id,
            draft_revision=int(world["draft_revision"]),
            source="manual",
            status="ready",
            content=dict(row[2]),
            directives=dict(row[3]),
            dependency_hashes=dict(row[4]),
            input_hash=str(row[5]),
            content_hash=str(row[6]),
            provenance=provenance,
        )
        stale_ids = _mark_downstream_stale(
            work,
            context,
            world=world,
            changed_topic_id=topic_id,
            changed_content_hash=str(row[6]),
        )
        work.commit()
    return {
        "ok": True,
        "topic": stored,
        "replaced_content_hash": str(current.get("content_hash") or ""),
        "stale_topic_ids": stale_ids,
    }
