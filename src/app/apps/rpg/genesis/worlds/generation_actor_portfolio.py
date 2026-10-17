"""Structured actor incentive and canonical relationship diversity certification."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_actor_incentives import (
    actor_incentive_components,
)

_DIVERSITY_COMPONENTS = (
    "dependency_type",
    "alliance_preference",
    "conflict_preference",
)
_STRUCTURAL_RELATIONSHIP_FIELDS = {
    "location_id",
    "place_id",
    "place_ids",
    "region_id",
    "region_ids",
    "culture_id",
    "culture_ids",
    "starting_place_id",
    "parent_place_id",
    "controlled_place_ids",
    "affected_place_ids",
}
_STRUCTURAL_RELATIONSHIP_KINDS = {
    "located_in",
    "present_at",
    "within_realm",
    "part_of",
    "region",
    "place",
    "location",
    "culture",
    "starting_place",
    "parent_place",
    "controlled_place",
    "affected_place",
}
_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class ActorPortfolioIssue:
    code: str
    actor_id: str
    path: str
    message: str
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "actor_id": self.actor_id,
            "path": self.path,
            "message": self.message,
            "evidence": dict(self.evidence),
            "severity": "error",
            "blocking": True,
        }


class ActorPortfolioCompilationError(ValueError):
    def __init__(self, issues: Sequence[ActorPortfolioIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.actor_id}:{issue.path}" for issue in self.issues
        )
        super().__init__("actor_portfolio_integrity_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "actor_portfolio_integrity_failed",
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


def _actor_domains(topic_graph: Mapping[str, Any] | None) -> set[str]:
    graph = _mapping(topic_graph)
    domains: set[str] = set()
    for node in _rows(graph.get("nodes")):
        topic_id = str(node.get("topic_id") or "")
        metadata = _mapping(node.get("metadata"))
        contract = _mapping(metadata.get("actor_incentive_contract"))
        if topic_id and bool(contract.get("required")):
            domains.add(topic_id)
    contract = _mapping(_mapping(graph.get("metadata")).get("actor_incentive_contract"))
    domains.update(str(value) for value in contract.get("domain_ids") or () if str(value))
    return domains


def _field_definitions(
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, tuple[dict[str, Any], ...]]:
    graph = _mapping(topic_graph)
    definitions: dict[str, tuple[dict[str, Any], ...]] = {}
    for node in _rows(graph.get("nodes")):
        topic_id = str(node.get("topic_id") or "")
        metadata = _mapping(node.get("metadata"))
        if topic_id:
            definitions[topic_id] = _rows(metadata.get("field_definitions"))
    return definitions


def _normalise_category(value: Any) -> str:
    rendered = "_".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))
    return rendered


def _signature_payload(value: Any) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return None, ("actor_incentive_signature_must_be_object",)
    row = dict(value)
    payload: dict[str, str] = {}
    issues: list[str] = []
    for component in actor_incentive_components():
        normalised = _normalise_category(row.get(component))
        if not normalised:
            issues.append(f"actor_incentive_component_required:{component}")
            continue
        if not _CATEGORY.fullmatch(normalised):
            issues.append(f"actor_incentive_component_invalid:{component}")
            continue
        payload[component] = normalised
    return (payload if not issues else None), tuple(issues)


def _fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _entity_rows(
    topic_rows: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, int, dict[str, Any]], ...]:
    values: list[tuple[str, int, dict[str, Any]]] = []
    for topic_index, raw_topic in enumerate(topic_rows, start=1):
        topic = _mapping(raw_topic)
        candidate = _candidate(topic)
        topic_id = str(
            topic.get("topic_id")
            or candidate.get("topic_id")
            or f"topic:{topic_index}"
        )
        for index, entity in enumerate(_rows(candidate.get("entities"))):
            values.append((topic_id, index, entity))
    return tuple(values)


def _entity_registry(
    entities: Sequence[tuple[str, int, Mapping[str, Any]]],
) -> dict[str, str]:
    return {
        str(entity.get("id") or ""): topic_id
        for topic_id, _index, entity in entities
        if str(entity.get("id") or "")
    }


def _reference_values(value: Any, value_type: str) -> tuple[str, ...]:
    if value_type == "entity_ref":
        rendered = str(value or "").strip()
        return (rendered,) if rendered else ()
    if value_type == "entity_ref_list" and isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes),
    ):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _relationship_kind(definition: Mapping[str, Any]) -> str:
    semantic_role = str(definition.get("semantic_role") or "").strip()
    if semantic_role:
        return semantic_role
    field_id = str(definition.get("field_id") or "references").strip()
    if field_id.endswith("_ids"):
        return field_id[:-4] or "references"
    if field_id.endswith("_id"):
        return field_id[:-3] or "references"
    return field_id or "references"


def _is_meaningful(field_id: str, kind: str) -> bool:
    return (
        field_id not in _STRUCTURAL_RELATIONSHIP_FIELDS
        and kind not in _STRUCTURAL_RELATIONSHIP_KINDS
    )


def _add_edge(
    profiles: dict[str, set[str]],
    *,
    actor_ids: set[str],
    registry: Mapping[str, str],
    source_id: str,
    target_id: str,
    kind: str,
) -> None:
    if not source_id or not target_id or source_id == target_id:
        return
    if source_id in actor_ids:
        profiles[source_id].add(
            f"out:{kind}:{registry.get(target_id, 'unknown')}:{target_id}"
        )
    if target_id in actor_ids:
        profiles[target_id].add(
            f"in:{kind}:{registry.get(source_id, 'unknown')}:{source_id}"
        )


def _relationship_profiles(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
    actor_ids: set[str],
    entities: Sequence[tuple[str, int, Mapping[str, Any]]],
) -> dict[str, tuple[str, ...]]:
    definitions = _field_definitions(topic_graph)
    registry = _entity_registry(entities)
    profiles: dict[str, set[str]] = {actor_id: set() for actor_id in actor_ids}
    for topic_id, _index, entity in entities:
        source_id = str(entity.get("id") or "").strip()
        if not source_id:
            continue
        for definition in definitions.get(topic_id, ()):
            value_type = str(definition.get("value_type") or "")
            if value_type not in {"entity_ref", "entity_ref_list"}:
                continue
            field_id = str(definition.get("field_id") or "")
            kind = _relationship_kind(definition)
            if not _is_meaningful(field_id, kind):
                continue
            for target_id in _reference_values(entity.get(field_id), value_type):
                _add_edge(
                    profiles,
                    actor_ids=actor_ids,
                    registry=registry,
                    source_id=source_id,
                    target_id=target_id,
                    kind=kind,
                )
    for raw_topic in topic_rows:
        candidate = _candidate(_mapping(raw_topic))
        for relationship in _rows(candidate.get("relationships")):
            kind = str(relationship.get("kind") or relationship.get("type") or "references")
            if not _is_meaningful("", kind):
                continue
            _add_edge(
                profiles,
                actor_ids=actor_ids,
                registry=registry,
                source_id=str(relationship.get("source_id") or relationship.get("source") or ""),
                target_id=str(relationship.get("target_id") or relationship.get("target") or ""),
                kind=kind,
            )
    return {actor_id: tuple(sorted(values)) for actor_id, values in sorted(profiles.items())}


def actor_portfolio_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[ActorPortfolioIssue, ...]:
    required_domains = _actor_domains(topic_graph)
    entities = _entity_rows(topic_rows)
    actors = [
        (topic_id, index, entity)
        for topic_id, index, entity in entities
        if topic_id in required_domains or "incentive_signature" in entity
    ]
    issues: list[ActorPortfolioIssue] = []
    payloads: dict[str, dict[str, str]] = {}
    actor_paths: dict[str, str] = {}
    for topic_id, index, actor in actors:
        actor_id = str(actor.get("id") or f"{topic_id}:actor:{index + 1}")
        path = f"/{topic_id}/entities/{index}/incentive_signature"
        actor_paths[actor_id] = path
        signature = actor.get("incentive_signature")
        if signature is None:
            issues.append(
                ActorPortfolioIssue(
                    code="actor_incentive_signature_required",
                    actor_id=actor_id,
                    path=path,
                    message="Graph-owned actor domains require a structured incentive signature.",
                    evidence={"topic_id": topic_id},
                )
            )
            continue
        payload, signature_issues = _signature_payload(signature)
        for code in signature_issues:
            issues.append(
                ActorPortfolioIssue(
                    code=code,
                    actor_id=actor_id,
                    path=path,
                    message="Actor incentive signature does not satisfy the categorical contract.",
                    evidence={"topic_id": topic_id, "signature": dict(signature) if isinstance(signature, Mapping) else signature},
                )
            )
        if payload is not None:
            payloads[actor_id] = payload

    actor_ids = {str(actor.get("id") or "") for _topic, _index, actor in actors}
    actor_ids.discard("")
    profiles = _relationship_profiles(topic_rows, topic_graph, actor_ids, entities)
    actor_count = len(actors)
    if actor_count >= 4:
        by_signature: dict[str, list[str]] = {}
        for actor_id, payload in payloads.items():
            by_signature.setdefault(_fingerprint(payload), []).append(actor_id)
        for fingerprint, repeated in sorted(by_signature.items()):
            if len(repeated) < 2:
                continue
            issues.append(
                ActorPortfolioIssue(
                    code="duplicate_actor_incentive_signature",
                    actor_id=repeated[0],
                    path=actor_paths.get(repeated[0], ""),
                    message="Multiple actors share the same complete incentive signature.",
                    evidence={"fingerprint": fingerprint, "actor_ids": sorted(repeated)},
                )
            )
        for component in _DIVERSITY_COMPONENTS:
            values = sorted({payload.get(component, "") for payload in payloads.values() if payload.get(component)})
            if len(values) < 2:
                issues.append(
                    ActorPortfolioIssue(
                        code="actor_incentive_component_uniform",
                        actor_id="",
                        path=f"/actor_portfolio/{component}",
                        message="Actor dependency, alliance, and conflict incentives require portfolio diversity.",
                        evidence={"component": component, "values": values, "actor_count": actor_count},
                    )
                )
        for actor_id, profile in profiles.items():
            if profile:
                continue
            issues.append(
                ActorPortfolioIssue(
                    code="actor_without_meaningful_relationship",
                    actor_id=actor_id,
                    path=f"/actor_portfolio/relationships/{actor_id}",
                    message="Each actor must participate in at least one non-spatial canonical relationship.",
                    evidence={"relationship_profile": []},
                )
            )
        relationship_kinds = {
            descriptor.split(":", 2)[1]
            for profile in profiles.values()
            for descriptor in profile
            if descriptor.count(":") >= 2
        }
        if len(relationship_kinds) < 2:
            issues.append(
                ActorPortfolioIssue(
                    code="actor_relationship_kind_diversity_low",
                    actor_id="",
                    path="/actor_portfolio/relationship_kinds",
                    message="The actor portfolio requires at least two meaningful canonical relationship kinds.",
                    evidence={"relationship_kinds": sorted(relationship_kinds)},
                )
            )
        unique_profiles = {_fingerprint({"edges": list(profile)}) for profile in profiles.values() if profile}
        minimum_profiles = max(2, math.ceil(actor_count / 3))
        if len(unique_profiles) < minimum_profiles:
            issues.append(
                ActorPortfolioIssue(
                    code="actor_relationship_portfolio_too_uniform",
                    actor_id="",
                    path="/actor_portfolio/relationship_profiles",
                    message="Canonical actor edge profiles are too uniform for the portfolio size.",
                    evidence={
                        "actor_count": actor_count,
                        "unique_profile_count": len(unique_profiles),
                        "minimum_profile_count": minimum_profiles,
                        "profiles": {actor_id: list(profile) for actor_id, profile in profiles.items()},
                    },
                )
            )
    unique = {
        (issue.code, issue.actor_id, issue.path): issue
        for issue in issues
    }
    return tuple(unique[key] for key in sorted(unique))


def actor_portfolio_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    required_domains = _actor_domains(topic_graph)
    entities = _entity_rows(topic_rows)
    actors = [
        entity
        for topic_id, _index, entity in entities
        if topic_id in required_domains or "incentive_signature" in entity
    ]
    actor_ids = {str(actor.get("id") or "") for actor in actors if str(actor.get("id") or "")}
    profiles = _relationship_profiles(topic_rows, topic_graph, actor_ids, entities)
    valid_payloads = {
        actor_id: payload
        for actor in actors
        if (actor_id := str(actor.get("id") or ""))
        and (payload := _signature_payload(actor.get("incentive_signature"))[0]) is not None
    }
    issues = actor_portfolio_issues(topic_rows, topic_graph)
    component_diversity = {
        component: len({payload.get(component, "") for payload in valid_payloads.values() if payload.get(component)})
        for component in actor_incentive_components()
    }
    relationship_kinds = sorted(
        {
            descriptor.split(":", 2)[1]
            for profile in profiles.values()
            for descriptor in profile
            if descriptor.count(":") >= 2
        }
    )
    return {
        "schema_version": "rpg_world_actor_portfolio_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "checks": {
            "required_domain_count": len(required_domains),
            "actor_count": len(actors),
            "valid_signature_count": len(valid_payloads),
            "unique_signature_count": len({_fingerprint(payload) for payload in valid_payloads.values()}),
            "component_diversity": component_diversity,
            "relationship_kind_count": len(relationship_kinds),
            "relationship_kinds": relationship_kinds,
            "unique_relationship_profile_count": len(
                {_fingerprint({"edges": list(profile)}) for profile in profiles.values() if profile}
            ),
            "actors_without_relationships": sorted(
                actor_id for actor_id, profile in profiles.items() if not profile
            ),
        },
        "relationship_profiles": {
            actor_id: list(profile) for actor_id, profile in profiles.items()
        },
    }


def require_valid_actor_portfolio(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = actor_portfolio_issues(topic_rows, topic_graph)
    if issues:
        raise ActorPortfolioCompilationError(issues)
    return actor_portfolio_report(topic_rows, topic_graph)


__all__ = [
    "ActorPortfolioCompilationError",
    "ActorPortfolioIssue",
    "actor_portfolio_issues",
    "actor_portfolio_report",
    "require_valid_actor_portfolio",
]
