"""Impact-aware downstream invalidation for World Forge retry operations."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

_FOUNDATIONAL_TOPICS = {"setting_rules", "history_timeline", "regions"}
_CONTRACT_FIELDS = {
    "id",
    "name",
    "aliases",
    "region_id",
    "parent_place_id",
    "group_ids",
    "location_id",
    "actor_ids",
    "place_ids",
    "pressure_ids",
    "cause",
    "consequences",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def dependent_topic_ids(graph: Any, changed_topic_ids: Sequence[str]) -> tuple[str, ...]:
    """Return transitive dependants without adding them to the retry target set."""

    changed = {str(value) for value in changed_topic_ids if str(value)}
    dependants_by_topic: dict[str, set[str]] = {}
    order = []
    for node in graph.topological_order():
        order.append(str(node.topic_id))
        for dependency in node.dependencies:
            dependants_by_topic.setdefault(str(dependency), set()).add(str(node.topic_id))
    found: set[str] = set()
    pending = list(changed)
    while pending:
        topic_id = pending.pop()
        for dependant in dependants_by_topic.get(topic_id, ()):
            if dependant not in changed and dependant not in found:
                found.add(dependant)
                pending.append(dependant)
    return tuple(topic_id for topic_id in order if topic_id in found)


def _impact_action(
    source_topic_id: str,
    scope: Mapping[str, Any],
) -> tuple[str, str]:
    mode = str(scope.get("scope") or "topic")
    fields = {str(value) for value in scope.get("fields") or () if str(value)}
    if source_topic_id in _FOUNDATIONAL_TOPICS and mode == "topic":
        return (
            "regenerate",
            "A foundational world contract is being replaced.",
        )
    if fields & _CONTRACT_FIELDS:
        return (
            "invalidate",
            "A consumed identity or reference contract field may change.",
        )
    return (
        "revalidate",
        "Upstream content is being retried and downstream assumptions may be stale.",
    )


def build_retry_invalidation_records(
    graph: Any,
    changed_topic_ids: Sequence[str],
    retry_scopes: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Describe downstream impact without automatically regenerating dependants."""

    changed = tuple(dict.fromkeys(str(value) for value in changed_topic_ids if str(value)))
    scopes = {
        str(key): _mapping(value)
        for key, value in dict(retry_scopes or {}).items()
        if isinstance(value, Mapping)
    }
    dependants = dependent_topic_ids(graph, changed)
    records: dict[str, dict[str, Any]] = {}
    node_map = graph.node_map()
    for dependant in dependants:
        causal_sources = tuple(
            source
            for source in changed
            if source in set(node_map[dependant].dependencies)
            or dependant in dependent_topic_ids(graph, (source,))
        )
        actions = [_impact_action(source, scopes.get(source, {})) for source in causal_sources]
        priority = {"revalidate": 1, "invalidate": 2, "regenerate": 3}
        action = max((item[0] for item in actions), key=lambda item: priority[item])
        reasons = list(dict.fromkeys(item[1] for item in actions))
        records[dependant] = {
            "schema_version": "rpg_world_generation_staleness_v1",
            "topic_id": dependant,
            "status": "potentially_stale",
            "caused_by_topic_ids": list(causal_sources),
            "required_action": action,
            "reasons": reasons,
            "automatically_regenerated": False,
        }
    return records


def apply_stale_progress(
    progress: Mapping[str, Any] | None,
    records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    payload = dict(progress or {})
    stale = {
        str(key): dict(value)
        for key, value in records.items()
        if isinstance(value, Mapping)
    }
    payload["stale_topic_ids"] = sorted(stale)
    payload["stale_topics"] = stale
    if stale:
        payload["publication_blocked"] = True
    return payload


__all__ = [
    "apply_stale_progress",
    "build_retry_invalidation_records",
    "dependent_topic_ids",
]
