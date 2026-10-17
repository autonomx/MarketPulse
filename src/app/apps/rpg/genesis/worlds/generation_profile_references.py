"""Profile-driven typed-reference validation for assembled World Forge canon."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ProfileReferenceIssue:
    code: str
    source_domain: str
    entity_id: str
    field_id: str
    target_id: str
    allowed_target_domains: tuple[str, ...]
    actual_target_domain: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source_domain": self.source_domain,
            "entity_id": self.entity_id,
            "field_id": self.field_id,
            "target_id": self.target_id,
            "allowed_target_domains": list(self.allowed_target_domains),
            "actual_target_domain": self.actual_target_domain,
            "blocking": True,
        }


class ProfileReferenceCompilationError(ValueError):
    def __init__(self, issues: Sequence[ProfileReferenceIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.source_domain}:{issue.entity_id}:"
            f"{issue.field_id}:{issue.target_id}"
            for issue in self.issues
        )
        super().__init__("profile_reference_integrity_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "profile_reference_integrity_failed",
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


def _profile(topic_graph: Mapping[str, Any] | None) -> dict[str, Any]:
    graph = _mapping(topic_graph)
    metadata = _mapping(graph.get("metadata"))
    return _mapping(metadata.get("resolved_profile"))


def _reference_values(value: Any, value_type: str) -> tuple[str, ...]:
    if value_type == "entity_ref":
        rendered = str(value or "").strip()
        return (rendered,) if rendered else ()
    if value_type == "entity_ref_list":
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return ()
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def profile_reference_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[ProfileReferenceIssue, ...]:
    """Validate every profile-declared typed reference in generated entities."""

    profile = _profile(topic_graph)
    domains = _rows(profile.get("domains"))
    if not domains:
        return ()
    domain_map = {
        str(domain.get("domain_id") or ""): domain
        for domain in domains
        if str(domain.get("domain_id") or "")
    }
    entities_by_domain: dict[str, list[dict[str, Any]]] = {}
    target_domain_by_id: dict[str, str] = {}
    for index, raw_topic in enumerate(topic_rows, start=1):
        topic = _mapping(raw_topic)
        candidate = _candidate(topic)
        domain_id = str(
            topic.get("topic_id")
            or candidate.get("topic_id")
            or f"topic:{index}"
        )
        entities = list(_rows(candidate.get("entities")))
        entities_by_domain.setdefault(domain_id, []).extend(entities)
        for entity in entities:
            entity_id = str(entity.get("id") or "").strip()
            if entity_id:
                target_domain_by_id[entity_id] = domain_id

    issues: list[ProfileReferenceIssue] = []
    for domain_id, entities in entities_by_domain.items():
        domain = domain_map.get(domain_id)
        if domain is None:
            continue
        definitions = _rows(domain.get("fields"))
        reference_fields = tuple(
            definition
            for definition in definitions
            if str(definition.get("value_type") or "")
            in {"entity_ref", "entity_ref_list"}
        )
        for entity_index, entity in enumerate(entities, start=1):
            entity_id = str(entity.get("id") or f"{domain_id}:entity:{entity_index}")
            for definition in reference_fields:
                field_id = str(definition.get("field_id") or "")
                value_type = str(definition.get("value_type") or "")
                allowed = tuple(
                    str(item)
                    for item in definition.get("allowed_target_domains") or ()
                    if str(item)
                )
                for target_id in _reference_values(entity.get(field_id), value_type):
                    actual_domain = target_domain_by_id.get(target_id, "")
                    if not actual_domain:
                        issues.append(
                            ProfileReferenceIssue(
                                code="unresolved_profile_reference",
                                source_domain=domain_id,
                                entity_id=entity_id,
                                field_id=field_id,
                                target_id=target_id,
                                allowed_target_domains=allowed,
                            )
                        )
                    elif allowed and actual_domain not in allowed:
                        issues.append(
                            ProfileReferenceIssue(
                                code="profile_reference_wrong_domain",
                                source_domain=domain_id,
                                entity_id=entity_id,
                                field_id=field_id,
                                target_id=target_id,
                                allowed_target_domains=allowed,
                                actual_target_domain=actual_domain,
                            )
                        )
    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                issue.source_domain,
                issue.entity_id,
                issue.field_id,
                issue.target_id,
                issue.code,
            ),
        )
    )


def profile_reference_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    profile = _profile(topic_graph)
    issues = profile_reference_issues(topic_rows, topic_graph)
    return {
        "schema_version": "rpg_world_generation_profile_references_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "checks": {
            "profile_domains": len(_rows(profile.get("domains"))),
            "reference_issues": len(issues),
        },
    }


def require_valid_profile_references(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = profile_reference_issues(topic_rows, topic_graph)
    if issues:
        raise ProfileReferenceCompilationError(issues)
    return profile_reference_report(topic_rows, topic_graph)


__all__ = [
    "ProfileReferenceCompilationError",
    "ProfileReferenceIssue",
    "profile_reference_issues",
    "profile_reference_report",
    "require_valid_profile_references",
]
