"""Resolve full, selected, stale, failed, and review world-generation scopes."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicGraph

_NON_GENERATION_CATEGORIES = {"compiler", "audit", "index", "bootstrap"}


def _authoring(row: Mapping[str, Any]) -> dict[str, Any]:
    provenance = row.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    authoring = provenance.get("authoring")
    return dict(authoring) if isinstance(authoring, Mapping) else {}


def _generation_ids(graph: CampaignTopicGraph) -> tuple[str, ...]:
    return tuple(
        node.topic_id
        for node in graph.topological_order()
        if node.category not in _NON_GENERATION_CATEGORIES
    )


def _dependency_closure(
    graph: CampaignTopicGraph,
    selected: Sequence[str],
) -> tuple[str, ...]:
    nodes = graph.node_map()
    collected: set[str] = set()

    def add(topic_id: str) -> None:
        if topic_id in collected:
            return
        node = nodes.get(topic_id)
        if node is None:
            raise ValueError(f"unknown_generation_topic:{topic_id}")
        for dependency in node.dependencies:
            add(dependency)
        if node.category not in _NON_GENERATION_CATEGORIES:
            collected.add(topic_id)

    for topic_id in selected:
        add(topic_id)
    return tuple(
        node.topic_id for node in graph.topological_order() if node.topic_id in collected
    )


def resolve_generation_scope(
    graph: CampaignTopicGraph,
    *,
    scope: Mapping[str, Any] | None,
    strategy: str,
    topic_rows: Sequence[Mapping[str, Any]],
    latest_run: Mapping[str, Any] | None,
    replace_locked: bool = False,
) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, Any]]:
    raw = dict(scope or {})
    mode = str(raw.get("mode") or ("selected" if raw.get("topic_ids") else "full"))
    all_ids = _generation_ids(graph)
    rows = {str(row.get("topic_id") or ""): row for row in topic_rows}
    progress = dict((latest_run or {}).get("progress") or {})
    if mode == "full":
        selected = all_ids
    elif mode == "selected":
        selected = tuple(
            dict.fromkeys(
                str(value) for value in raw.get("topic_ids") or () if str(value)
            )
        )
    elif mode == "stale":
        selected = tuple(
            topic_id
            for topic_id in all_ids
            if str(rows.get(topic_id, {}).get("status") or "") == "stale"
        )
    elif mode == "failed":
        selected = tuple(
            topic_id
            for topic_id in progress.get("failed_topic_ids") or ()
            if str(topic_id) in all_ids
        )
    elif mode == "review":
        requested = tuple(
            dict.fromkeys(
                str(value) for value in raw.get("topic_ids") or () if str(value)
            )
        )
        reviewable = tuple(
            dict.fromkeys(
                str(topic_id)
                for key in (
                    "flagged_topic_ids",
                    "failed_topic_ids",
                    "blocked_topic_ids",
                )
                for topic_id in progress.get(key) or ()
                if str(topic_id) in all_ids
            )
        )
        selected = requested or reviewable
        invalid = sorted(set(selected) - set(reviewable))
        if invalid:
            raise ValueError(
                "generation_topics_not_reviewable:" + ",".join(invalid)
            )
    else:
        raise ValueError(f"invalid_generation_scope_mode:{mode}")
    if not selected:
        raise ValueError(f"generation_scope_empty:{mode}")
    unknown = sorted(set(selected) - set(all_ids))
    if unknown:
        raise ValueError("unknown_generation_topics:" + ",".join(unknown))
    targets = _dependency_closure(graph, selected)
    forced = tuple(selected) if strategy == "force" else ()
    locked = [
        topic_id
        for topic_id in forced
        if bool(_authoring(rows.get(topic_id, {})).get("generation_lock"))
        or str(rows.get(topic_id, {}).get("source") or "") == "manual"
    ]
    if locked and not replace_locked:
        raise ValueError("generation_topics_locked:" + ",".join(sorted(locked)))
    normalized = {
        "mode": mode,
        "topic_ids": list(selected),
        "resolved_topic_ids": list(targets),
        "include_dependencies": True,
        "replace_locked": bool(replace_locked),
    }
    return targets, forced, normalized
