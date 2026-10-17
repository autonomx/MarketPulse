"""Capability-specific network and surveillance constraint certification."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_network_constraints import (
    network_constraint_components,
)

_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FORBIDDEN_ABSOLUTES = {
    "coverage_scope": {
        "global",
        "universal",
        "ubiquitous",
        "total_coverage",
        "everywhere",
    },
    "latency_class": {
        "instant",
        "zero_latency",
        "immediate_everywhere",
        "no_delay",
    },
    "blind_spot": {
        "none",
        "no_blind_spots",
        "total_visibility",
        "perfect_visibility",
    },
    "traceability_limit": {
        "perfect",
        "total_traceability",
        "always_identified",
        "unlimited_traceability",
    },
    "failure_mode": {
        "none",
        "infallible",
        "no_failure",
        "cannot_fail",
    },
}
_DIVERSITY_COMPONENTS = ("blind_spot", "failure_mode")


@dataclass(frozen=True)
class NetworkConstraintIssue:
    code: str
    topic_id: str
    network_id: str
    path: str
    message: str
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "topic_id": self.topic_id,
            "network_id": self.network_id,
            "path": self.path,
            "message": self.message,
            "evidence": dict(self.evidence),
            "severity": "error",
            "blocking": True,
        }


class NetworkConstraintCompilationError(ValueError):
    def __init__(self, issues: Sequence[NetworkConstraintIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.topic_id}:{issue.network_id}:{issue.path}"
            for issue in self.issues
        )
        super().__init__("network_constraint_integrity_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "network_constraint_integrity_failed",
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


def _network_domains(topic_graph: Mapping[str, Any] | None) -> set[str]:
    graph = _mapping(topic_graph)
    domains: set[str] = set()
    for node in _rows(graph.get("nodes")):
        topic_id = str(node.get("topic_id") or "")
        contract = _mapping(
            _mapping(node.get("metadata")).get("network_constraint_contract")
        )
        if topic_id and bool(contract.get("required")):
            domains.add(topic_id)
    contract = _mapping(
        _mapping(graph.get("metadata")).get("network_constraint_contract")
    )
    domains.update(
        str(value) for value in contract.get("domain_ids") or () if str(value)
    )
    return domains


def _normalise_category(value: Any) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _signature_payload(value: Any) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return None, ("network_constraint_signature_must_be_object",)
    row = dict(value)
    payload: dict[str, str] = {}
    issues: list[str] = []
    for component in network_constraint_components():
        normalised = _normalise_category(row.get(component))
        if not normalised:
            issues.append(f"network_constraint_component_required:{component}")
            continue
        if not _CATEGORY.fullmatch(normalised):
            issues.append(f"network_constraint_component_invalid:{component}")
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


def _network_id(topic_id: str, index: int, network: Mapping[str, Any]) -> str:
    return str(network.get("id") or f"{topic_id}:network:{index + 1}")


def _network_rows(
    topic_rows: Sequence[Mapping[str, Any]],
    required_domains: set[str],
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
            if topic_id in required_domains or "network_constraint_signature" in entity:
                values.append((topic_id, index, entity))
    return tuple(values)


def network_constraint_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[NetworkConstraintIssue, ...]:
    required_domains = _network_domains(topic_graph)
    rows = _network_rows(topic_rows, required_domains)
    issues: list[NetworkConstraintIssue] = []
    payloads: dict[str, dict[str, str]] = {}
    paths: dict[str, str] = {}
    topics: dict[str, str] = {}
    controllers: dict[str, tuple[str, ...]] = {}
    covered_places: dict[str, tuple[str, ...]] = {}
    for topic_id, index, network in rows:
        network_id = _network_id(topic_id, index, network)
        path = f"/{topic_id}/entities/{index}"
        paths[network_id] = path
        topics[network_id] = topic_id
        controllers[network_id] = _id_list(network.get("controller_group_ids"))
        covered_places[network_id] = _id_list(network.get("covered_place_ids"))
        if not controllers[network_id]:
            issues.append(
                NetworkConstraintIssue(
                    code="network_controller_required",
                    topic_id=topic_id,
                    network_id=network_id,
                    path=f"{path}/controller_group_ids",
                    message=(
                        "Capability-constrained networks require at least one "
                        "canonical controller group."
                    ),
                    evidence={"controller_group_ids": []},
                )
            )
        if not covered_places[network_id]:
            issues.append(
                NetworkConstraintIssue(
                    code="network_coverage_required",
                    topic_id=topic_id,
                    network_id=network_id,
                    path=f"{path}/covered_place_ids",
                    message=(
                        "Capability-constrained networks require explicit canonical "
                        "place coverage."
                    ),
                    evidence={"covered_place_ids": []},
                )
            )
        signature = network.get("network_constraint_signature")
        if signature is None:
            issues.append(
                NetworkConstraintIssue(
                    code="network_constraint_signature_required",
                    topic_id=topic_id,
                    network_id=network_id,
                    path=f"{path}/network_constraint_signature",
                    message=(
                        "The active digital-spaces capability requires a bounded "
                        "network signature."
                    ),
                    evidence={},
                )
            )
            continue
        payload, signature_issues = _signature_payload(signature)
        for code in signature_issues:
            issues.append(
                NetworkConstraintIssue(
                    code=code,
                    topic_id=topic_id,
                    network_id=network_id,
                    path=f"{path}/network_constraint_signature",
                    message=(
                        "Network constraint signature does not satisfy the "
                        "categorical contract."
                    ),
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
        payloads[network_id] = payload
        for component, forbidden_values in _FORBIDDEN_ABSOLUTES.items():
            value = payload.get(component, "")
            if value in forbidden_values:
                issues.append(
                    NetworkConstraintIssue(
                        code="unbounded_network_constraint",
                        topic_id=topic_id,
                        network_id=network_id,
                        path=(
                            f"{path}/network_constraint_signature/{component}"
                        ),
                        message=(
                            "Networks must expose bounded coverage, latency, blind "
                            "spots, traceability, and failure modes."
                        ),
                        evidence={"component": component, "value": value},
                    )
                )

    if len(rows) >= 3:
        by_fingerprint: dict[str, list[str]] = {}
        for network_id, payload in payloads.items():
            by_fingerprint.setdefault(_fingerprint(payload), []).append(network_id)
        for fingerprint, network_ids in sorted(by_fingerprint.items()):
            if len(network_ids) < 2:
                continue
            first_id = sorted(network_ids)[0]
            issues.append(
                NetworkConstraintIssue(
                    code="duplicate_network_constraint_signature",
                    topic_id=topics.get(first_id, ""),
                    network_id=first_id,
                    path=f"{paths[first_id]}/network_constraint_signature",
                    message=(
                        "Multiple networks share the same complete operational "
                        "constraint signature."
                    ),
                    evidence={
                        "fingerprint": fingerprint,
                        "network_ids": sorted(network_ids),
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
                    NetworkConstraintIssue(
                        code="network_constraint_portfolio_too_uniform",
                        topic_id="",
                        network_id="",
                        path=f"/network_constraint_portfolio/{component}",
                        message=(
                            "Network portfolios require more than one blind spot "
                            "and failure mode."
                        ),
                        evidence={"component": component, "values": values},
                    )
                )
        distinct_places = sorted(
            {
                place_id
                for values in covered_places.values()
                for place_id in values
            }
        )
        if len(distinct_places) < 2:
            issues.append(
                NetworkConstraintIssue(
                    code="network_coverage_portfolio_too_narrow",
                    topic_id="",
                    network_id="",
                    path="/network_constraint_portfolio/covered_place_ids",
                    message=(
                        "A multi-network world must identify coverage across at "
                        "least two canonical places."
                    ),
                    evidence={"covered_place_ids": distinct_places},
                )
            )
    unique = {
        (issue.code, issue.topic_id, issue.network_id, issue.path): issue
        for issue in issues
    }
    return tuple(unique[key] for key in sorted(unique))


def network_constraint_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    required_domains = _network_domains(topic_graph)
    rows = _network_rows(topic_rows, required_domains)
    payloads = {
        network_id: payload
        for topic_id, index, network in rows
        if (network_id := _network_id(topic_id, index, network))
        and (
            payload := _signature_payload(
                network.get("network_constraint_signature")
            )[0]
        )
        is not None
    }
    issues = network_constraint_issues(topic_rows, topic_graph)
    controllers = sorted(
        {
            controller_id
            for _topic, _index, network in rows
            for controller_id in _id_list(network.get("controller_group_ids"))
        }
    )
    places = sorted(
        {
            place_id
            for _topic, _index, network in rows
            for place_id in _id_list(network.get("covered_place_ids"))
        }
    )
    return {
        "schema_version": "rpg_world_network_constraint_portfolio_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "checks": {
            "required_domain_count": len(required_domains),
            "network_count": len(rows),
            "valid_signature_count": len(payloads),
            "unique_signature_count": len(
                {_fingerprint(payload) for payload in payloads.values()}
            ),
            "controller_count": len(controllers),
            "covered_place_count": len(places),
            "component_diversity": {
                component: len(
                    {
                        payload.get(component, "")
                        for payload in payloads.values()
                        if payload.get(component)
                    }
                )
                for component in network_constraint_components()
            },
        },
        "controller_group_ids": controllers,
        "covered_place_ids": places,
    }


def require_valid_network_constraints(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = network_constraint_issues(topic_rows, topic_graph)
    if issues:
        raise NetworkConstraintCompilationError(issues)
    return network_constraint_report(topic_rows, topic_graph)


__all__ = [
    "NetworkConstraintCompilationError",
    "NetworkConstraintIssue",
    "network_constraint_issues",
    "network_constraint_report",
    "require_valid_network_constraints",
]
