"""Revisioned, selectively consumed planning contracts for World Forge."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class PlanningTopicDefinition:
    topic_id: str
    dependencies: tuple[str, ...] = ()
    consumers: tuple[str, ...] = ()
    revision: int = 1
    internal: bool = True
    publish_as_authoring_page: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "dependencies": list(self.dependencies),
            "consumers": list(self.consumers),
            "revision": self.revision,
            "internal": self.internal,
            "publish_as_authoring_page": self.publish_as_authoring_page,
        }


_PLANNING_TOPICS = (
    PlanningTopicDefinition(
        "world_invariants",
        consumers=(
            "setting_rules",
            "history_timeline",
            "regions",
            "groups",
            "cultures",
        ),
    ),
    PlanningTopicDefinition(
        "anchor_registry",
        dependencies=("world_invariants",),
        consumers=(
            "history_timeline",
            "regions",
            "places",
            "groups",
            "cultures",
            "actors",
        ),
    ),
    PlanningTopicDefinition(
        "geography_resource_plan",
        dependencies=("world_invariants", "anchor_registry"),
        consumers=("regions", "places", "groups", "economy_law"),
    ),
    PlanningTopicDefinition(
        "historical_epoch_plan",
        dependencies=(
            "world_invariants",
            "anchor_registry",
            "geography_resource_plan",
        ),
        consumers=(
            "history_timeline",
            "regions",
            "places",
            "groups",
            "cultures",
            "causal_links",
        ),
    ),
    PlanningTopicDefinition(
        "present_day_state",
        dependencies=("historical_epoch_plan",),
        consumers=("regions", "places", "groups", "pressures"),
    ),
    PlanningTopicDefinition(
        "political_claim_graph",
        dependencies=("present_day_state",),
        consumers=("groups", "actors", "pressures", "quests"),
    ),
    PlanningTopicDefinition(
        "settlement_origin_plan",
        dependencies=(
            "geography_resource_plan",
            "historical_epoch_plan",
            "present_day_state",
        ),
        consumers=("places", "groups", "economy_law", "causal_links"),
    ),
    PlanningTopicDefinition(
        "culture_lineage_plan",
        dependencies=("historical_epoch_plan", "present_day_state"),
        consumers=("cultures", "actors", "groups", "causal_links"),
    ),
    PlanningTopicDefinition(
        "pressure_plan",
        dependencies=(
            "present_day_state",
            "political_claim_graph",
            "settlement_origin_plan",
        ),
        consumers=(
            "pressures",
            "quests",
            "encounter_seeds",
            "opening_threads",
        ),
    ),
    PlanningTopicDefinition(
        "opening_scope_plan",
        dependencies=("pressure_plan",),
        consumers=(
            "places",
            "actors",
            "groups",
            "opening_threads",
            "opening_scenarios",
        ),
    ),
)


def planning_topic_definitions() -> tuple[PlanningTopicDefinition, ...]:
    return _PLANNING_TOPICS


def validate_planning_contract(
    definitions: Iterable[PlanningTopicDefinition] | None = None,
) -> tuple[str, ...]:
    rows = tuple(definitions or _PLANNING_TOPICS)
    by_id = {row.topic_id: row for row in rows}
    issues: list[str] = []
    if len(by_id) != len(rows):
        issues.append("duplicate_planning_topic_id")
    for row in rows:
        if not row.topic_id:
            issues.append("missing_planning_topic_id")
        if row.revision < 1:
            issues.append(f"invalid_planning_revision:{row.topic_id}")
        if not row.internal or row.publish_as_authoring_page:
            issues.append(f"planning_topic_not_internal:{row.topic_id}")
        for dependency in row.dependencies:
            if dependency not in by_id:
                issues.append(f"unknown_planning_dependency:{row.topic_id}:{dependency}")
    pending = {row.topic_id: set(row.dependencies) for row in rows}
    while pending:
        ready = {topic_id for topic_id, dependencies in pending.items() if not dependencies}
        if not ready:
            issues.append("planning_dependency_cycle")
            break
        for topic_id in ready:
            pending.pop(topic_id)
        for dependencies in pending.values():
            dependencies.difference_update(ready)
    return tuple(dict.fromkeys(issues))


def planning_revision_hash(
    definitions: Iterable[PlanningTopicDefinition] | None = None,
) -> str:
    rows = tuple(definitions or _PLANNING_TOPICS)
    issues = validate_planning_contract(rows)
    if issues:
        raise ValueError("invalid_world_forge_planning_contract:" + ",".join(issues))
    encoded = json.dumps(
        [row.as_dict() for row in rows],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def planning_slice_for_topic(
    topic_id: str,
    planning_topics: Mapping[str, Any],
) -> dict[str, Any]:
    """Return only the planning artefacts explicitly consumed by one domain."""

    selected: dict[str, Any] = {}
    for definition in _PLANNING_TOPICS:
        if topic_id in definition.consumers and definition.topic_id in planning_topics:
            selected[definition.topic_id] = planning_topics[definition.topic_id]
    return selected


def planning_contract_metadata() -> dict[str, Any]:
    issues = validate_planning_contract()
    if issues:
        raise ValueError("invalid_world_forge_planning_contract:" + ",".join(issues))
    return {
        "schema_version": "rpg_world_forge_planning_contract_v1",
        "revision_hash": planning_revision_hash(),
        "internal": True,
        "publish_as_authoring_pages": False,
        "topics": [row.as_dict() for row in _PLANNING_TOPICS],
    }


__all__ = [
    "PlanningTopicDefinition",
    "planning_contract_metadata",
    "planning_revision_hash",
    "planning_slice_for_topic",
    "planning_topic_definitions",
    "validate_planning_contract",
]
