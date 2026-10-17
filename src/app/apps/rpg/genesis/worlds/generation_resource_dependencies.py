"""Canonical resource dependency and failure-propagation certification."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_resource_dependencies import (
    resource_dependency_components,
)

_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FORBIDDEN_VALUES = {
    "supply_mode": {
        "self_sufficient",
        "unlimited",
        "spontaneous",
        "needs_nothing",
    },
    "dependency_strength": {
        "none",
        "independent",
        "self_sufficient",
    },
    "bottleneck_type": {
        "none",
        "no_bottleneck",
        "unlimited_capacity",
    },
    "depletion_horizon": {
        "never",
        "infinite",
        "unlimited",
    },
    "failure_consequence": {
        "none",
        "no_effect",
        "harmless",
    },
    "recovery_mode": {
        "instant",
        "automatic",
        "none_needed",
    },
}
_DIVERSITY_COMPONENTS = (
    "resource_class",
    "bottleneck_type",
    "failure_consequence",
)
_CHOKEPOINT_STRENGTHS = {"critical", "single_source"}
_CHOKEPOINT_TYPES = {
    "single_source",
    "transport_chokepoint",
    "licensed_access",
    "specialist_labour",
    "political_embargo",
}
_NO_SUBSTITUTE = {"no_substitute", "none", "unavailable"}


@dataclass(frozen=True)
class ResourceDependencyIssue:
    code: str
    topic_id: str
    resource_entity_id: str
    path: str
    message: str
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "topic_id": self.topic_id,
            "resource_entity_id": self.resource_entity_id,
            "path": self.path,
            "message": self.message,
            "evidence": dict(self.evidence),
            "severity": "error",
            "blocking": True,
        }


class ResourceDependencyCompilationError(ValueError):
    def __init__(self, issues: Sequence[ResourceDependencyIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.topic_id}:{issue.resource_entity_id}:{issue.path}"
            for issue in self.issues
        )
        super().__init__("resource_dependency_integrity_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "resource_dependency_integrity_failed",
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


def _resource_domains(topic_graph: Mapping[str, Any] | None) -> set[str]:
    graph = _mapping(topic_graph)
    domains: set[str] = set()
    for node in _rows(graph.get("nodes")):
        topic_id = str(node.get("topic_id") or "")
        contract = _mapping(
            _mapping(node.get("metadata")).get("resource_dependency_contract")
        )
        if topic_id and bool(contract.get("required")):
            domains.add(topic_id)
    contract = _mapping(
        _mapping(graph.get("metadata")).get("resource_dependency_contract")
    )
    domains.update(
        str(value) for value in contract.get("domain_ids") or () if str(value)
    )
    return domains


def _normalise_category(value: Any) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _signature_payload(value: Any) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return None, ("resource_dependency_signature_must_be_object",)
    row = dict(value)
    payload: dict[str, str] = {}
    issues: list[str] = []
    for component in resource_dependency_components():
        normalised = _normalise_category(row.get(component))
        if not normalised:
            issues.append(f"resource_dependency_component_required:{component}")
            continue
        if not _CATEGORY.fullmatch(normalised):
            issues.append(f"resource_dependency_component_invalid:{component}")
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


def _id_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


def _all_entities(
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


def _resource_rows(
    entities: Sequence[tuple[str, int, Mapping[str, Any]]],
    required_domains: set[str],
) -> tuple[tuple[str, int, dict[str, Any]], ...]:
    return tuple(
        (topic_id, index, dict(entity))
        for topic_id, index, entity in entities
        if topic_id in required_domains
        or "resource_dependency_signature" in entity
    )


def resource_dependency_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[ResourceDependencyIssue, ...]:
    required_domains = _resource_domains(topic_graph)
    entities = _all_entities(topic_rows)
    rows = _resource_rows(entities, required_domains)
    registry = {
        str(entity.get("id") or ""): topic_id
        for topic_id, _index, entity in entities
        if str(entity.get("id") or "")
    }
    issues: list[ResourceDependencyIssue] = []
    payloads: dict[str, dict[str, str]] = {}
    paths: dict[str, str] = {}
    topics: dict[str, str] = {}
    providers_by_entity: dict[str, tuple[str, ...]] = {}
    consumers_by_entity: dict[str, tuple[str, ...]] = {}
    for topic_id, index, entity in rows:
        entity_id = str(entity.get("id") or f"{topic_id}:resource:{index + 1}")
        path = f"/{topic_id}/entities/{index}"
        paths[entity_id] = path
        topics[entity_id] = topic_id
        providers = _id_list(entity.get("resource_provider_ids"))
        consumers = _id_list(entity.get("resource_consumer_ids"))
        providers_by_entity[entity_id] = providers
        consumers_by_entity[entity_id] = consumers
        if not providers:
            issues.append(
                ResourceDependencyIssue(
                    code="resource_provider_required",
                    topic_id=topic_id,
                    resource_entity_id=entity_id,
                    path=f"{path}/resource_provider_ids",
                    message="Resource-bearing entities require canonical input providers.",
                    evidence={"resource_provider_ids": []},
                )
            )
        if not consumers:
            issues.append(
                ResourceDependencyIssue(
                    code="resource_consumer_required",
                    topic_id=topic_id,
                    resource_entity_id=entity_id,
                    path=f"{path}/resource_consumer_ids",
                    message="Resource-bearing entities require canonical downstream consumers.",
                    evidence={"resource_consumer_ids": []},
                )
            )
        unknown_providers = sorted(set(providers) - set(registry))
        unknown_consumers = sorted(set(consumers) - set(registry))
        if unknown_providers:
            issues.append(
                ResourceDependencyIssue(
                    code="resource_provider_unknown",
                    topic_id=topic_id,
                    resource_entity_id=entity_id,
                    path=f"{path}/resource_provider_ids",
                    message="Every resource provider must resolve to canonical world identity.",
                    evidence={"unknown_provider_ids": unknown_providers},
                )
            )
        if unknown_consumers:
            issues.append(
                ResourceDependencyIssue(
                    code="resource_consumer_unknown",
                    topic_id=topic_id,
                    resource_entity_id=entity_id,
                    path=f"{path}/resource_consumer_ids",
                    message="Every resource consumer must resolve to canonical world identity.",
                    evidence={"unknown_consumer_ids": unknown_consumers},
                )
            )
        if providers and consumers and set(providers) == set(consumers):
            issues.append(
                ResourceDependencyIssue(
                    code="resource_provider_consumer_sets_identical",
                    topic_id=topic_id,
                    resource_entity_id=entity_id,
                    path=path,
                    message="Provider and consumer sets must describe a real supply transition.",
                    evidence={
                        "resource_provider_ids": list(providers),
                        "resource_consumer_ids": list(consumers),
                    },
                )
            )
        signature = entity.get("resource_dependency_signature")
        if signature is None:
            issues.append(
                ResourceDependencyIssue(
                    code="resource_dependency_signature_required",
                    topic_id=topic_id,
                    resource_entity_id=entity_id,
                    path=f"{path}/resource_dependency_signature",
                    message="Resource-bearing entities require bounded dependency semantics.",
                    evidence={},
                )
            )
            continue
        payload, signature_issues = _signature_payload(signature)
        for code in signature_issues:
            issues.append(
                ResourceDependencyIssue(
                    code=code,
                    topic_id=topic_id,
                    resource_entity_id=entity_id,
                    path=f"{path}/resource_dependency_signature",
                    message="Resource dependency signature does not satisfy the categorical contract.",
                    evidence={
                        "signature": (
                            dict(signature)
                            if isinstance(signature, Mapping)
                            else signature
                        )
                    },
                )
            )
        if payload is None:
            continue
        payloads[entity_id] = payload
        for component, forbidden_values in _FORBIDDEN_VALUES.items():
            value = payload.get(component, "")
            if value in forbidden_values:
                issues.append(
                    ResourceDependencyIssue(
                        code="unbounded_resource_dependency",
                        topic_id=topic_id,
                        resource_entity_id=entity_id,
                        path=f"{path}/resource_dependency_signature/{component}",
                        message="Resource systems must depend on finite supply and concrete recovery.",
                        evidence={"component": component, "value": value},
                    )
                )

    if len(rows) >= 4:
        by_fingerprint: dict[str, list[str]] = {}
        for entity_id, payload in payloads.items():
            by_fingerprint.setdefault(_fingerprint(payload), []).append(entity_id)
        for fingerprint, entity_ids in sorted(by_fingerprint.items()):
            if len(entity_ids) < 2:
                continue
            first_id = sorted(entity_ids)[0]
            issues.append(
                ResourceDependencyIssue(
                    code="duplicate_resource_dependency_signature",
                    topic_id=topics.get(first_id, ""),
                    resource_entity_id=first_id,
                    path=f"{paths[first_id]}/resource_dependency_signature",
                    message="Multiple resource systems share the same complete dependency shape.",
                    evidence={
                        "fingerprint": fingerprint,
                        "resource_entity_ids": sorted(entity_ids),
                    },
                )
            )
        for component in _DIVERSITY_COMPONENTS:
            values = sorted(
                {
                    payload.get(component, "")
                    for payload in payloads.values()
                    if payload.get(component)
                }
            )
            if len(values) < 2:
                issues.append(
                    ResourceDependencyIssue(
                        code="resource_dependency_portfolio_too_uniform",
                        topic_id="",
                        resource_entity_id="",
                        path=f"/resource_dependency_portfolio/{component}",
                        message="Resource portfolios require varied classes, bottlenecks, and failure effects.",
                        evidence={"component": component, "values": values},
                    )
                )
        has_chokepoint = any(
            payload.get("dependency_strength") in _CHOKEPOINT_STRENGTHS
            or payload.get("bottleneck_type") in _CHOKEPOINT_TYPES
            or payload.get("substitute_class") in _NO_SUBSTITUTE
            for payload in payloads.values()
        )
        if not has_chokepoint:
            issues.append(
                ResourceDependencyIssue(
                    code="resource_portfolio_missing_chokepoint",
                    topic_id="",
                    resource_entity_id="",
                    path="/resource_dependency_portfolio/chokepoints",
                    message="At least one resource chain must expose a meaningful chokepoint.",
                    evidence={},
                )
            )
        has_substitute = any(
            payload.get("substitute_class")
            and payload.get("substitute_class") not in _NO_SUBSTITUTE
            for payload in payloads.values()
        )
        if not has_substitute:
            issues.append(
                ResourceDependencyIssue(
                    code="resource_portfolio_missing_substitute",
                    topic_id="",
                    resource_entity_id="",
                    path="/resource_dependency_portfolio/substitutes",
                    message="At least one resource chain must expose a viable substitute or fallback.",
                    evidence={},
                )
            )
        all_providers = {
            provider_id
            for values in providers_by_entity.values()
            for provider_id in values
            if provider_id in registry
        }
        all_consumers = {
            consumer_id
            for values in consumers_by_entity.values()
            for consumer_id in values
            if consumer_id in registry
        }
        if len(all_providers) < 2:
            issues.append(
                ResourceDependencyIssue(
                    code="resource_provider_portfolio_too_narrow",
                    topic_id="",
                    resource_entity_id="",
                    path="/resource_dependency_portfolio/providers",
                    message="The world must not route every resource through one provider identity.",
                    evidence={"resource_provider_ids": sorted(all_providers)},
                )
            )
        if len(all_consumers) < 2:
            issues.append(
                ResourceDependencyIssue(
                    code="resource_consumer_portfolio_too_narrow",
                    topic_id="",
                    resource_entity_id="",
                    path="/resource_dependency_portfolio/consumers",
                    message="The world must expose more than one downstream resource consumer.",
                    evidence={"resource_consumer_ids": sorted(all_consumers)},
                )
            )
    unique = {
        (issue.code, issue.topic_id, issue.resource_entity_id, issue.path): issue
        for issue in issues
    }
    return tuple(unique[key] for key in sorted(unique))


def resource_dependency_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    required_domains = _resource_domains(topic_graph)
    entities = _all_entities(topic_rows)
    rows = _resource_rows(entities, required_domains)
    registry = {
        str(entity.get("id") or "")
        for _topic_id, _index, entity in entities
        if str(entity.get("id") or "")
    }
    payloads = {
        entity_id: payload
        for topic_id, index, entity in rows
        if (entity_id := str(entity.get("id") or f"{topic_id}:resource:{index + 1}"))
        and (
            payload := _signature_payload(
                entity.get("resource_dependency_signature")
            )[0]
        )
        is not None
    }
    providers = sorted(
        {
            provider_id
            for _topic, _index, entity in rows
            for provider_id in _id_list(entity.get("resource_provider_ids"))
            if provider_id in registry
        }
    )
    consumers = sorted(
        {
            consumer_id
            for _topic, _index, entity in rows
            for consumer_id in _id_list(entity.get("resource_consumer_ids"))
            if consumer_id in registry
        }
    )
    issues = resource_dependency_issues(topic_rows, topic_graph)
    return {
        "schema_version": "rpg_world_resource_dependency_portfolio_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "checks": {
            "required_domain_count": len(required_domains),
            "resource_entity_count": len(rows),
            "valid_signature_count": len(payloads),
            "unique_signature_count": len(
                {_fingerprint(payload) for payload in payloads.values()}
            ),
            "provider_count": len(providers),
            "consumer_count": len(consumers),
            "has_chokepoint": any(
                payload.get("dependency_strength") in _CHOKEPOINT_STRENGTHS
                or payload.get("bottleneck_type") in _CHOKEPOINT_TYPES
                or payload.get("substitute_class") in _NO_SUBSTITUTE
                for payload in payloads.values()
            ),
            "has_substitute": any(
                payload.get("substitute_class")
                and payload.get("substitute_class") not in _NO_SUBSTITUTE
                for payload in payloads.values()
            ),
            "component_diversity": {
                component: len(
                    {
                        payload.get(component, "")
                        for payload in payloads.values()
                        if payload.get(component)
                    }
                )
                for component in resource_dependency_components()
            },
        },
        "resource_provider_ids": providers,
        "resource_consumer_ids": consumers,
        "dependencies": [
            {
                "resource_entity_id": str(entity.get("id") or ""),
                "provider_ids": list(_id_list(entity.get("resource_provider_ids"))),
                "consumer_ids": list(_id_list(entity.get("resource_consumer_ids"))),
            }
            for _topic, _index, entity in rows
        ],
    }


def require_valid_resource_dependencies(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = resource_dependency_issues(topic_rows, topic_graph)
    if issues:
        raise ResourceDependencyCompilationError(issues)
    return resource_dependency_report(topic_rows, topic_graph)


__all__ = [
    "ResourceDependencyCompilationError",
    "ResourceDependencyIssue",
    "require_valid_resource_dependencies",
    "resource_dependency_issues",
    "resource_dependency_report",
]
