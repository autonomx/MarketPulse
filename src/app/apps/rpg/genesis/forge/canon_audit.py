"""Structured consistency audit for generated Campaign Bible material."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .world_forge_causal_audit import audit_causal_canon
from .world_forge_generation import GeneratedTopic

_ALLOWED_VISIBILITY = {
    "public",
    "player_known",
    "learned",
    "partially_known",
    "disputed",
    "hidden_from_player",
    "npc_private",
    "faction_private",
    "game_master_canon",
}


@dataclass(frozen=True)
class CanonPatch:
    operation: str
    collection: str
    item_id: str
    field: str = ""
    value: Any = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "collection": self.collection,
            "item_id": self.item_id,
            "field": self.field,
            "value": self.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CanonAuditIssue:
    code: str
    message: str
    item_id: str = ""
    severity: str = "error"
    patch: CanonPatch | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "item_id": self.item_id,
            "severity": self.severity,
        }
        if self.patch is not None:
            payload["patch"] = self.patch.as_dict()
        return payload


@dataclass(frozen=True)
class CanonAuditReport:
    passed: bool
    issues: tuple[CanonAuditIssue, ...] = ()
    patches: tuple[CanonPatch, ...] = ()
    checks: Mapping[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": [issue.as_dict() for issue in self.issues],
            "patches": [patch.as_dict() for patch in self.patches],
            "checks": dict(self.checks),
        }


def _rows(topics: Iterable[GeneratedTopic], field_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for topic in topics:
        value = getattr(topic, field_name)
        out.extend(dict(row) for row in value if isinstance(row, Mapping))
    return out


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple | set):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _item_id(row: Mapping[str, Any], fallback: str) -> str:
    return str(row.get("id") or row.get("document_id") or row.get("evidence_id") or fallback)


def _duplicates(collection: str, rows: list[dict[str, Any]]) -> list[CanonAuditIssue]:
    seen: set[str] = set()
    issues: list[CanonAuditIssue] = []
    for index, row in enumerate(rows, start=1):
        item_id = _item_id(row, f"{collection}:{index}")
        if item_id in seen:
            issues.append(
                CanonAuditIssue(
                    "duplicate_id",
                    f"Duplicate identifier in {collection}: {item_id}",
                    item_id,
                    patch=CanonPatch(
                        "remove",
                        collection,
                        item_id,
                        reason="Keep one canonical item per identifier.",
                    ),
                )
            )
        seen.add(item_id)
    return issues


def _visibility_issues(collection: str, rows: list[dict[str, Any]]) -> list[CanonAuditIssue]:
    issues: list[CanonAuditIssue] = []
    for index, row in enumerate(rows, start=1):
        item_id = _item_id(row, f"{collection}:{index}")
        visibility = str(row.get("visibility") or "game_master_canon")
        if visibility not in _ALLOWED_VISIBILITY:
            patch = CanonPatch(
                "replace",
                collection,
                item_id,
                "visibility",
                "game_master_canon",
                "Unknown visibility defaults to non-player canon.",
            )
            issues.append(CanonAuditIssue("invalid_visibility", f"Unsupported visibility: {visibility}", item_id, patch=patch))
        if item_id.startswith("secret:") and visibility in {"public", "player_known", "learned"}:
            patch = CanonPatch(
                "replace",
                collection,
                item_id,
                "visibility",
                "npc_private",
                "Secrets cannot begin as public player knowledge.",
            )
            issues.append(CanonAuditIssue("public_secret", "A secret is exposed as public knowledge.", item_id, patch=patch))
    return issues


def _relationship_issues(
    relationships: list[dict[str, Any]],
    entity_ids: set[str],
) -> list[CanonAuditIssue]:
    issues: list[CanonAuditIssue] = []
    for index, row in enumerate(relationships, start=1):
        item_id = _item_id(row, f"relationship:{index}")
        source_id = str(row.get("source_id") or "")
        target_id = str(row.get("target_id") or "")
        for field, value in (("source_id", source_id), ("target_id", target_id)):
            if not value or value not in entity_ids:
                issues.append(
                    CanonAuditIssue(
                        "dangling_relationship_endpoint",
                        f"Relationship {field} does not resolve: {value or '<missing>'}",
                        item_id,
                        patch=CanonPatch(
                            "remove",
                            "relationships",
                            item_id,
                            reason="A relationship may not reference an unknown entity.",
                        ),
                    )
                )
    return issues


def _knowledge_issues(
    facts: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    entity_ids: set[str],
) -> list[CanonAuditIssue]:
    issues: list[CanonAuditIssue] = []
    fact_ids = {_item_id(row, f"fact:{index}") for index, row in enumerate(facts, start=1)}
    for index, row in enumerate(facts, start=1):
        item_id = _item_id(row, f"fact:{index}")
        for actor_id in _strings(row.get("known_by")):
            if actor_id not in entity_ids:
                issues.append(CanonAuditIssue("unknown_knower", f"Fact is assigned to unknown knower {actor_id}.", item_id))
        owner = str(row.get("secret_owner_id") or "")
        if owner and owner not in entity_ids:
            issues.append(CanonAuditIssue("unknown_secret_owner", f"Secret owner does not exist: {owner}", item_id))
    for index, row in enumerate(rules, start=1):
        item_id = _item_id(row, f"knowledge_rule:{index}")
        evidence_id = str(row.get("evidence_id") or "")
        if evidence_id and evidence_id not in fact_ids:
            issues.append(
                CanonAuditIssue(
                    "dangling_knowledge_rule",
                    f"Knowledge rule references unknown evidence {evidence_id}.",
                    item_id,
                    patch=CanonPatch("remove", "knowledge_rules", item_id, reason="Remove an ACL without a fact."),
                )
            )
    return issues


def _temporal_issues(entities: list[dict[str, Any]], facts: list[dict[str, Any]]) -> list[CanonAuditIssue]:
    issues: list[CanonAuditIssue] = []
    for collection, rows in (("entities", entities), ("facts", facts)):
        for index, row in enumerate(rows, start=1):
            item_id = _item_id(row, f"{collection}:{index}")
            start = _integer(row.get("start_year") or row.get("founded_year") or row.get("birth_year"))
            end = _integer(row.get("end_year") or row.get("dissolved_year") or row.get("death_year"))
            current = _integer(row.get("current_year"))
            age = _integer(row.get("age"))
            birth = _integer(row.get("birth_year"))
            if start is not None and end is not None and end < start:
                issues.append(CanonAuditIssue("reversed_date_range", f"End year {end} precedes start year {start}.", item_id))
            if age is not None and birth is not None and current is not None and current - birth != age:
                issues.append(
                    CanonAuditIssue(
                        "age_mismatch",
                        f"Age {age} does not match birth year {birth} at year {current}.",
                        item_id,
                        patch=CanonPatch("replace", collection, item_id, "age", current - birth, "Derive age from canonical years."),
                    )
                )
    return issues


def _geography_issues(entities: list[dict[str, Any]], entity_ids: set[str]) -> list[CanonAuditIssue]:
    issues: list[CanonAuditIssue] = []
    parent_by_id: dict[str, str] = {}
    for row in entities:
        item_id = str(row.get("id") or "")
        parent = str(row.get("region_id") or row.get("realm_id") or row.get("parent_id") or "")
        if parent:
            if parent not in entity_ids:
                issues.append(CanonAuditIssue("unknown_geographic_parent", f"Unknown geographic parent {parent}.", item_id))
            parent_by_id[item_id] = parent
        for route in row.get("travel_routes") or () if isinstance(row.get("travel_routes"), list | tuple) else ():
            if isinstance(route, Mapping):
                target = str(route.get("target_id") or route.get("to") or "")
                if target and target not in entity_ids:
                    issues.append(CanonAuditIssue("unknown_travel_endpoint", f"Unknown travel endpoint {target}.", item_id))
    for item_id in parent_by_id:
        visited: set[str] = set()
        current = item_id
        while current in parent_by_id:
            if current in visited:
                issues.append(CanonAuditIssue("geography_cycle", "Geographic containment forms a cycle.", item_id))
                break
            visited.add(current)
            current = parent_by_id[current]
    return issues


def audit_generated_canon(
    topics: Iterable[GeneratedTopic],
    *,
    compiled_relationships: Iterable[Mapping[str, Any]] = (),
) -> CanonAuditReport:
    topic_list = tuple(topics)
    entities = _rows(topic_list, "entities")
    facts = _rows(topic_list, "facts")
    documents = _rows(topic_list, "documents")
    knowledge_rules = _rows(topic_list, "knowledge_rules")
    relationships = [*_rows(topic_list, "relationships"), *(dict(row) for row in compiled_relationships)]
    entity_ids = {str(row.get("id") or "") for row in entities if str(row.get("id") or "")}
    issues: list[CanonAuditIssue] = []
    for collection, rows in (
        ("entities", entities),
        ("facts", facts),
        ("documents", documents),
        ("relationships", relationships),
        ("knowledge_rules", knowledge_rules),
    ):
        issues.extend(_duplicates(collection, rows))
        issues.extend(_visibility_issues(collection, rows))
    issues.extend(_relationship_issues(relationships, entity_ids))
    issues.extend(_knowledge_issues(facts, knowledge_rules, entity_ids))
    issues.extend(_temporal_issues(entities, facts))
    issues.extend(_geography_issues(entities, entity_ids))
    causal_findings = audit_causal_canon(topic_list)
    issues.extend(
        CanonAuditIssue(
            finding.code,
            finding.message,
            finding.item_id,
            severity=finding.severity,
        )
        for finding in causal_findings
    )
    patches = tuple(issue.patch for issue in issues if issue.patch is not None)
    error_count = sum(1 for issue in issues if issue.severity == "error")
    return CanonAuditReport(
        passed=error_count == 0,
        issues=tuple(issues),
        patches=patches,
        checks={
            "topics": len(topic_list),
            "documents": len(documents),
            "entities": len(entities),
            "facts": len(facts),
            "relationships": len(relationships),
            "knowledge_rules": len(knowledge_rules),
            "causal_findings": len(causal_findings),
            "errors": error_count,
        },
    )
