"""World-level structured conflict-anchor concentration certification."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

_DEFAULT_POLICY = {
    "dominant_conflict_ratio": 0.5,
    "dominant_conflict_min_entities": 4,
    "dominant_conflict_min_topics": 2,
}
_FALLBACK_CONFLICT_TOPICS = {
    "pressures",
    "quests",
    "encounter_seeds",
    "opening_threads",
    "opening_scenarios",
    "threats",
}
_CONFLICT_TARGET_DOMAINS = {
    "pressures",
    "groups",
    "actors",
    "threats",
    "opening_threads",
}
_CONFLICT_REFERENCE_FIELDS = {
    "pressure_id",
    "pressure_ids",
    "group_id",
    "group_ids",
    "actor_id",
    "actor_ids",
    "initial_actor_ids",
    "threat_id",
    "threat_ids",
    "opening_thread_id",
    "opening_thread_ids",
    "antagonist_id",
    "antagonist_ids",
    "opposition_id",
    "opposition_ids",
    "rival_id",
    "rival_ids",
    "controller_group_id",
    "controller_group_ids",
}


@dataclass(frozen=True)
class ConflictAnchorOccurrence:
    topic_id: str
    entity_id: str
    entity_name: str
    source_fields: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "source_fields": list(self.source_fields),
        }


@dataclass(frozen=True)
class ConflictPortfolioIssue:
    code: str
    anchor: str
    count: int
    entity_ratio: float
    topic_count: int
    occurrences: tuple[ConflictAnchorOccurrence, ...]
    message: str
    budget: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "anchor": self.anchor,
            "count": self.count,
            "entity_ratio": self.entity_ratio,
            "topic_count": self.topic_count,
            "occurrences": [row.as_dict() for row in self.occurrences],
            "message": self.message,
            "budget": dict(self.budget),
            "severity": "error",
            "blocking": True,
        }


class ConflictPortfolioCompilationError(ValueError):
    def __init__(self, issues: Sequence[ConflictPortfolioIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.anchor}:{issue.count}:{issue.entity_ratio:.4f}"
            for issue in self.issues
        )
        super().__init__("conflict_portfolio_integrity_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "conflict_portfolio_integrity_failed",
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("candidate", "content"):
        value = row.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return dict(row)


def _policy(topic_graph: Mapping[str, Any] | None) -> dict[str, Any]:
    graph = _mapping(topic_graph)
    configured = _mapping(_mapping(graph.get("metadata")).get("conflict_portfolio_policy"))
    return {**_DEFAULT_POLICY, **configured}


def _graph_contract(
    topic_graph: Mapping[str, Any] | None,
) -> tuple[set[str], dict[str, set[str]]]:
    graph = _mapping(topic_graph)
    conflict_topics = set(_FALLBACK_CONFLICT_TOPICS)
    reference_fields: dict[str, set[str]] = {}
    for node in _rows(graph.get("nodes")):
        topic_id = str(node.get("topic_id") or "")
        metadata = _mapping(node.get("metadata"))
        semantic_roles = {
            str(value) for value in metadata.get("semantic_roles") or () if str(value)
        }
        mission_contract = _mapping(metadata.get("mission_signature_contract"))
        if "initial_conflict" in semantic_roles or bool(mission_contract.get("required")):
            conflict_topics.add(topic_id)
        for definition in _rows(metadata.get("field_definitions")):
            field_id = str(definition.get("field_id") or "")
            value_type = str(definition.get("value_type") or "")
            allowed = {
                str(value)
                for value in definition.get("allowed_target_domains") or ()
                if str(value)
            }
            if (
                field_id
                and value_type in {"entity_ref", "entity_ref_list"}
                and allowed.intersection(_CONFLICT_TARGET_DOMAINS)
            ):
                reference_fields.setdefault(topic_id, set()).add(field_id)
    metadata = _mapping(graph.get("metadata"))
    mission_contract = _mapping(metadata.get("mission_signature_contract"))
    conflict_topics.update(
        str(value) for value in mission_contract.get("domain_ids") or () if str(value)
    )
    return conflict_topics, reference_fields


def _reference_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        rendered = value.strip()
        return (rendered,) if rendered else ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _mission_signature_anchors(entity: Mapping[str, Any]) -> dict[str, set[str]]:
    signature = _mapping(entity.get("mission_signature"))
    anchors: dict[str, set[str]] = {}
    antagonist = str(signature.get("antagonist") or "").strip().casefold()
    pressure = str(signature.get("pressure") or "").strip().casefold()
    if antagonist:
        anchors.setdefault(f"signature:antagonist:{antagonist}", set()).add(
            "mission_signature.antagonist"
        )
    if pressure:
        anchors.setdefault(f"signature:pressure:{pressure}", set()).add(
            "mission_signature.pressure"
        )
    return anchors


def _entity_anchors(
    entity: Mapping[str, Any],
    *,
    topic_id: str,
    configured_fields: set[str],
) -> dict[str, set[str]]:
    fields = set(configured_fields)
    fields.update(
        field_id for field_id in _CONFLICT_REFERENCE_FIELDS if field_id in entity
    )
    anchors: dict[str, set[str]] = {}
    for field_id in sorted(fields):
        for value in _reference_values(entity.get(field_id)):
            if value == str(entity.get("id") or ""):
                continue
            anchors.setdefault(value, set()).add(field_id)
    for anchor, source_fields in _mission_signature_anchors(entity).items():
        anchors.setdefault(anchor, set()).update(source_fields)
    return anchors


def _conflict_entities(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[
    tuple[tuple[str, str, str], ...],
    dict[str, tuple[ConflictAnchorOccurrence, ...]],
]:
    conflict_topics, reference_fields = _graph_contract(topic_graph)
    entities: list[tuple[str, str, str]] = []
    occurrences: dict[str, list[ConflictAnchorOccurrence]] = {}
    for topic_index, raw_topic in enumerate(topic_rows, start=1):
        topic = _mapping(raw_topic)
        candidate = _candidate(topic)
        topic_id = str(
            topic.get("topic_id")
            or candidate.get("topic_id")
            or f"topic:{topic_index}"
        )
        if topic_id not in conflict_topics:
            continue
        for entity_index, entity in enumerate(_rows(candidate.get("entities")), start=1):
            entity_id = str(entity.get("id") or f"{topic_id}:entity:{entity_index}")
            entity_name = str(entity.get("name") or entity.get("title") or entity_id)
            entities.append((topic_id, entity_id, entity_name))
            anchors = _entity_anchors(
                entity,
                topic_id=topic_id,
                configured_fields=reference_fields.get(topic_id, set()),
            )
            for anchor, source_fields in anchors.items():
                occurrences.setdefault(anchor, []).append(
                    ConflictAnchorOccurrence(
                        topic_id=topic_id,
                        entity_id=entity_id,
                        entity_name=entity_name,
                        source_fields=tuple(sorted(source_fields)),
                    )
                )
    unique_entities = {
        (topic_id, entity_id): (topic_id, entity_id, name)
        for topic_id, entity_id, name in entities
    }
    unique_occurrences = {
        anchor: tuple(
            sorted(
                {
                    (row.topic_id, row.entity_id): row for row in rows
                }.values(),
                key=lambda row: (row.topic_id, row.entity_id, row.entity_name),
            )
        )
        for anchor, rows in sorted(occurrences.items())
    }
    return (
        tuple(unique_entities[key] for key in sorted(unique_entities)),
        unique_occurrences,
    )


def _core_conflicts(topic_graph: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    graph = _mapping(topic_graph)
    metadata = _mapping(graph.get("metadata"))
    values = []
    for index, item in enumerate(metadata.get("core_conflicts") or (), start=1):
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        conflict_id = str(row.get("conflict_id") or f"core-conflict:{index}")
        anchors = sorted(
            {str(value) for value in row.get("anchors") or () if str(value)}
        )
        entity_ids = sorted(
            {str(value) for value in row.get("entity_ids") or () if str(value)}
        )
        if anchors and entity_ids:
            values.append(
                {
                    "conflict_id": conflict_id,
                    "anchors": anchors,
                    "entity_ids": entity_ids,
                }
            )
    return tuple(sorted(values, key=lambda row: str(row["conflict_id"])))


def _declared_core_conflict(
    anchor: str,
    occurrences: Sequence[ConflictAnchorOccurrence],
    declarations: Sequence[Mapping[str, Any]],
) -> str:
    entity_ids = {row.entity_id for row in occurrences}
    for declaration in declarations:
        anchors = {str(value) for value in declaration.get("anchors") or ()}
        scoped_ids = {str(value) for value in declaration.get("entity_ids") or ()}
        if anchor in anchors and entity_ids.issubset(scoped_ids):
            return str(declaration.get("conflict_id") or "")
    return ""


def conflict_portfolio_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[ConflictPortfolioIssue, ...]:
    """Detect one undeclared conflict anchor dominating important content."""

    policy = _policy(topic_graph)
    entities, anchor_map = _conflict_entities(topic_rows, topic_graph)
    total = len(entities)
    if total == 0:
        return ()
    declarations = _core_conflicts(topic_graph)
    maximum_ratio = float(policy["dominant_conflict_ratio"])
    minimum_entities = int(policy["dominant_conflict_min_entities"])
    minimum_topics = int(policy["dominant_conflict_min_topics"])
    issues: list[ConflictPortfolioIssue] = []
    for anchor, occurrences in anchor_map.items():
        count = len(occurrences)
        ratio = count / total
        topic_count = len({row.topic_id for row in occurrences})
        if (
            count < minimum_entities
            or ratio <= maximum_ratio
            or topic_count < minimum_topics
            or _declared_core_conflict(anchor, occurrences, declarations)
        ):
            continue
        issues.append(
            ConflictPortfolioIssue(
                code="dominant_undeclared_conflict_anchor",
                anchor=anchor,
                count=count,
                entity_ratio=round(ratio, 6),
                topic_count=topic_count,
                occurrences=occurrences,
                message=(
                    "One structured conflict anchor dominates important content without "
                    "an exact graph-level core-conflict declaration."
                ),
                budget={
                    "maximum_ratio": maximum_ratio,
                    "minimum_entities": minimum_entities,
                    "minimum_topics": minimum_topics,
                },
            )
        )
    return tuple(
        sorted(issues, key=lambda issue: (issue.anchor, issue.code))
    )


def conflict_portfolio_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    policy = _policy(topic_graph)
    entities, anchor_map = _conflict_entities(topic_rows, topic_graph)
    issues = conflict_portfolio_issues(topic_rows, topic_graph)
    return {
        "schema_version": "rpg_world_conflict_portfolio_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "policy": policy,
        "declared_core_conflicts": list(_core_conflicts(topic_graph)),
        "checks": {
            "conflict_entity_count": len(entities),
            "tracked_anchor_count": len(anchor_map),
            "dominant_anchor_count": len(issues),
        },
    }


def require_valid_conflict_portfolio(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = conflict_portfolio_issues(topic_rows, topic_graph)
    if issues:
        raise ConflictPortfolioCompilationError(issues)
    return conflict_portfolio_report(topic_rows, topic_graph)


__all__ = [
    "ConflictAnchorOccurrence",
    "ConflictPortfolioCompilationError",
    "ConflictPortfolioIssue",
    "conflict_portfolio_issues",
    "conflict_portfolio_report",
    "require_valid_conflict_portfolio",
]
