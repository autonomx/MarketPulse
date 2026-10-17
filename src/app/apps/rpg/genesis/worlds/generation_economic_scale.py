"""Representative economic scale and internal consistency certification."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_economic_scale import (
    economic_scale_components,
)

_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_POPULATION_RANKS = {
    "dozens": 0,
    "hundreds": 1,
    "thousands": 2,
    "tens_of_thousands": 3,
    "hundreds_of_thousands": 4,
}
_WORKFORCE_RANKS = {
    "individual": 0,
    "small_crew": 1,
    "dozens": 2,
    "hundreds": 3,
    "thousands": 4,
}
_SERVICE_REACH_RANKS = {
    "household": 0,
    "neighbourhood": 1,
    "district": 2,
    "multi_district": 3,
    "regional": 4,
}
_THROUGHPUT_RANKS = {
    "bespoke": 0,
    "dozens_per_day": 1,
    "hundreds_per_day": 2,
    "thousands_per_day": 3,
    "continuous_bulk": 4,
}
_PRICE_BASES = {
    "labour_cost",
    "scarcity_markup",
    "regulated_tariff",
    "barter_equivalence",
    "risk_premium",
    "subscription",
    "ration_credit",
}
_SCARCITY_RANKS = {
    "abundant": 0,
    "stable": 1,
    "constrained": 2,
    "scarce": 3,
    "critical": 4,
}
_RESERVE_RANKS = {
    "hours": 0,
    "days": 1,
    "weeks": 2,
    "months": 3,
    "seasonal": 4,
}
_DEMAND_PRESSURES = {
    "low",
    "steady",
    "elevated",
    "surging",
    "overloaded",
}
_ALLOWED_SCOPES = {"place_population", "service_system"}
_ALLOWED_VALUES: Mapping[str, set[str] | Mapping[str, int]] = {
    "scale_scope": _ALLOWED_SCOPES,
    "served_population_band": _POPULATION_RANKS,
    "workforce_band": _WORKFORCE_RANKS,
    "service_reach_band": _SERVICE_REACH_RANKS,
    "throughput_band": _THROUGHPUT_RANKS,
    "price_basis": _PRICE_BASES,
    "scarcity_level": _SCARCITY_RANKS,
    "reserve_horizon": _RESERVE_RANKS,
    "demand_pressure": _DEMAND_PRESSURES,
}
_FORBIDDEN_ABSOLUTES = {
    "served_population_band": {
        "everyone",
        "global",
        "infinite_population",
        "unbounded",
    },
    "service_reach_band": {
        "universal",
        "global",
        "everyone",
        "everywhere",
    },
    "throughput_band": {
        "unlimited",
        "infinite",
        "instant_bulk",
    },
    "price_basis": {
        "free",
        "no_cost",
        "arbitrary",
        "unpriced",
    },
    "reserve_horizon": {
        "infinite",
        "never_depletes",
        "unlimited",
    },
}
_DIVERSITY_COMPONENTS = (
    "served_population_band",
    "throughput_band",
    "price_basis",
    "scarcity_level",
)
_MIN_COVERAGE_BY_REACH = {
    "household": 1,
    "neighbourhood": 1,
    "district": 1,
    "multi_district": 2,
    "regional": 2,
}


@dataclass(frozen=True)
class EconomicScaleIssue:
    code: str
    topic_id: str
    entity_id: str
    path: str
    message: str
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "topic_id": self.topic_id,
            "entity_id": self.entity_id,
            "path": self.path,
            "message": self.message,
            "evidence": dict(self.evidence),
            "severity": "error",
            "blocking": True,
        }


class EconomicScaleCompilationError(ValueError):
    def __init__(self, issues: Sequence[EconomicScaleIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.topic_id}:{issue.entity_id}:{issue.path}"
            for issue in self.issues
        )
        super().__init__("economic_scale_integrity_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "economic_scale_integrity_failed",
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


def _scale_contracts(
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    graph = _mapping(topic_graph)
    contracts: dict[str, dict[str, Any]] = {}
    global_contract = _mapping(
        _mapping(graph.get("metadata")).get("economic_scale_contract")
    )
    global_domains = {
        str(value) for value in global_contract.get("domain_ids") or () if str(value)
    }
    for node in _rows(graph.get("nodes")):
        topic_id = str(node.get("topic_id") or "")
        contract = _mapping(
            _mapping(node.get("metadata")).get("economic_scale_contract")
        )
        if topic_id and (bool(contract.get("required")) or topic_id in global_domains):
            contracts[topic_id] = contract
    for topic_id in global_domains:
        contracts.setdefault(topic_id, {})
    return contracts


def _normalise_category(value: Any) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _signature_payload(value: Any) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return None, ("economic_scale_signature_must_be_object",)
    row = dict(value)
    payload: dict[str, str] = {}
    issues: list[str] = []
    for component in economic_scale_components():
        normalised = _normalise_category(row.get(component))
        if not normalised:
            issues.append(f"economic_scale_component_required:{component}")
            continue
        if not _CATEGORY.fullmatch(normalised):
            issues.append(f"economic_scale_component_invalid:{component}")
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


def _scale_rows(
    entities: Sequence[tuple[str, int, Mapping[str, Any]]],
    contracts: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[str, int, dict[str, Any]], ...]:
    return tuple(
        (topic_id, index, dict(entity))
        for topic_id, index, entity in entities
        if topic_id in contracts or "economic_scale_signature" in entity
    )


def _rank_issue(
    *,
    code: str,
    topic_id: str,
    entity_id: str,
    path: str,
    message: str,
    evidence: Mapping[str, Any],
) -> EconomicScaleIssue:
    return EconomicScaleIssue(
        code=code,
        topic_id=topic_id,
        entity_id=entity_id,
        path=path,
        message=message,
        evidence=evidence,
    )


def economic_scale_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[EconomicScaleIssue, ...]:
    contracts = _scale_contracts(topic_graph)
    entities = _all_entities(topic_rows)
    rows = _scale_rows(entities, contracts)
    registry = {
        str(entity.get("id") or "")
        for _topic, _index, entity in entities
        if str(entity.get("id") or "")
    }
    topics_present = {topic_id for topic_id, _index, _entity in rows}
    issues: list[EconomicScaleIssue] = []
    payloads: dict[str, dict[str, str]] = {}
    paths: dict[str, str] = {}
    topics: dict[str, str] = {}
    coverage_by_entity: dict[str, tuple[str, ...]] = {}
    for required_topic in sorted(set(contracts) - topics_present):
        issues.append(
            EconomicScaleIssue(
                code="economic_scale_domain_missing",
                topic_id=required_topic,
                entity_id="",
                path=f"/{required_topic}",
                message="A graph-contracted economic scale domain has no generated entities.",
                evidence={},
            )
        )
    for topic_id, index, entity in rows:
        entity_id = str(entity.get("id") or f"{topic_id}:scale:{index + 1}")
        path = f"/{topic_id}/entities/{index}"
        paths[entity_id] = path
        topics[entity_id] = topic_id
        contract = _mapping(contracts.get(topic_id))
        coverage_field = str(contract.get("coverage_field") or "")
        coverage_ids = _id_list(entity.get(coverage_field)) if coverage_field else ()
        coverage_by_entity[entity_id] = coverage_ids
        if coverage_field and not coverage_ids:
            issues.append(
                EconomicScaleIssue(
                    code="economic_service_coverage_required",
                    topic_id=topic_id,
                    entity_id=entity_id,
                    path=f"{path}/{coverage_field}",
                    message="Service-system scale must be grounded in canonical affected places.",
                    evidence={"coverage_field": coverage_field},
                )
            )
        unknown_coverage = sorted(set(coverage_ids) - registry)
        if unknown_coverage:
            issues.append(
                EconomicScaleIssue(
                    code="economic_service_coverage_unknown",
                    topic_id=topic_id,
                    entity_id=entity_id,
                    path=f"{path}/{coverage_field}",
                    message="Every service coverage endpoint must resolve to canonical world identity.",
                    evidence={"unknown_coverage_ids": unknown_coverage},
                )
            )
        signature = entity.get("economic_scale_signature")
        if signature is None:
            issues.append(
                EconomicScaleIssue(
                    code="economic_scale_signature_required",
                    topic_id=topic_id,
                    entity_id=entity_id,
                    path=f"{path}/economic_scale_signature",
                    message="Places and economy systems require representative scale bands.",
                    evidence={},
                )
            )
            continue
        payload, signature_issues = _signature_payload(signature)
        for code in signature_issues:
            issues.append(
                EconomicScaleIssue(
                    code=code,
                    topic_id=topic_id,
                    entity_id=entity_id,
                    path=f"{path}/economic_scale_signature",
                    message="Economic scale signature does not satisfy the categorical contract.",
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
        expected_scope = str(contract.get("expected_scope") or "")
        if expected_scope and payload.get("scale_scope") != expected_scope:
            issues.append(
                EconomicScaleIssue(
                    code="economic_scale_scope_mismatch",
                    topic_id=topic_id,
                    entity_id=entity_id,
                    path=f"{path}/economic_scale_signature/scale_scope",
                    message="Economic scale scope must match the graph-owned domain role.",
                    evidence={
                        "expected_scope": expected_scope,
                        "observed_scope": payload.get("scale_scope"),
                    },
                )
            )
        for component, allowed in _ALLOWED_VALUES.items():
            value = payload.get(component, "")
            if value not in allowed:
                issues.append(
                    EconomicScaleIssue(
                        code="economic_scale_band_unknown",
                        topic_id=topic_id,
                        entity_id=entity_id,
                        path=f"{path}/economic_scale_signature/{component}",
                        message="Economic scale comparisons require one of the contract's exact bands.",
                        evidence={"component": component, "value": value},
                    )
                )
        for component, forbidden in _FORBIDDEN_ABSOLUTES.items():
            value = payload.get(component, "")
            if value in forbidden:
                issues.append(
                    EconomicScaleIssue(
                        code="unbounded_economic_scale",
                        topic_id=topic_id,
                        entity_id=entity_id,
                        path=f"{path}/economic_scale_signature/{component}",
                        message="Population, service reach, throughput, price and reserves must be bounded.",
                        evidence={"component": component, "value": value},
                    )
                )
        population_rank = _POPULATION_RANKS.get(
            payload.get("served_population_band", "")
        )
        workforce_rank = _WORKFORCE_RANKS.get(payload.get("workforce_band", ""))
        reach_rank = _SERVICE_REACH_RANKS.get(
            payload.get("service_reach_band", "")
        )
        throughput_rank = _THROUGHPUT_RANKS.get(
            payload.get("throughput_band", "")
        )
        scarcity_rank = _SCARCITY_RANKS.get(payload.get("scarcity_level", ""))
        reserve_rank = _RESERVE_RANKS.get(payload.get("reserve_horizon", ""))
        if (
            population_rank is not None
            and workforce_rank is not None
            and workforce_rank > population_rank
        ):
            issues.append(
                _rank_issue(
                    code="economic_workforce_exceeds_population_scale",
                    topic_id=topic_id,
                    entity_id=entity_id,
                    path=f"{path}/economic_scale_signature",
                    message="Workforce scale cannot exceed the served population scale.",
                    evidence={
                        "served_population_band": payload["served_population_band"],
                        "workforce_band": payload["workforce_band"],
                    },
                )
            )
        if (
            population_rank is not None
            and reach_rank is not None
            and reach_rank > population_rank
        ):
            issues.append(
                _rank_issue(
                    code="economic_service_reach_exceeds_population_scale",
                    topic_id=topic_id,
                    entity_id=entity_id,
                    path=f"{path}/economic_scale_signature",
                    message="Service reach cannot exceed the represented population scale.",
                    evidence={
                        "served_population_band": payload["served_population_band"],
                        "service_reach_band": payload["service_reach_band"],
                    },
                )
            )
        if (
            workforce_rank is not None
            and throughput_rank is not None
            and throughput_rank > workforce_rank + 1
        ):
            issues.append(
                _rank_issue(
                    code="economic_throughput_exceeds_workforce_capacity",
                    topic_id=topic_id,
                    entity_id=entity_id,
                    path=f"{path}/economic_scale_signature",
                    message="Throughput must be representative of the available workforce band.",
                    evidence={
                        "workforce_band": payload["workforce_band"],
                        "throughput_band": payload["throughput_band"],
                    },
                )
            )
        if scarcity_rank is not None and reserve_rank is not None:
            if scarcity_rank >= 3 and reserve_rank >= 2:
                issues.append(
                    _rank_issue(
                        code="economic_scarcity_not_supported_by_reserve",
                        topic_id=topic_id,
                        entity_id=entity_id,
                        path=f"{path}/economic_scale_signature",
                        message="Severe scarcity requires hours-or-days reserves, not long stockpiles.",
                        evidence={
                            "scarcity_level": payload["scarcity_level"],
                            "reserve_horizon": payload["reserve_horizon"],
                        },
                    )
                )
            if scarcity_rank <= 1 and reserve_rank <= 1:
                issues.append(
                    _rank_issue(
                        code="economic_stability_not_supported_by_reserve",
                        topic_id=topic_id,
                        entity_id=entity_id,
                        path=f"{path}/economic_scale_signature",
                        message="Abundant or stable supply cannot rest on only hours or days of reserves.",
                        evidence={
                            "scarcity_level": payload["scarcity_level"],
                            "reserve_horizon": payload["reserve_horizon"],
                        },
                    )
                )
        if coverage_field:
            minimum_coverage = _MIN_COVERAGE_BY_REACH.get(
                payload.get("service_reach_band", ""),
                1,
            )
            if len(coverage_ids) < minimum_coverage:
                issues.append(
                    EconomicScaleIssue(
                        code="economic_service_coverage_below_reach_band",
                        topic_id=topic_id,
                        entity_id=entity_id,
                        path=f"{path}/{coverage_field}",
                        message="Canonical service coverage is too narrow for the declared reach band.",
                        evidence={
                            "service_reach_band": payload.get("service_reach_band"),
                            "coverage_count": len(coverage_ids),
                            "minimum_coverage_count": minimum_coverage,
                        },
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
                EconomicScaleIssue(
                    code="duplicate_economic_scale_signature",
                    topic_id=topics.get(first_id, ""),
                    entity_id=first_id,
                    path=f"{paths[first_id]}/economic_scale_signature",
                    message="Multiple entities share the same complete economic scale signature.",
                    evidence={
                        "fingerprint": fingerprint,
                        "entity_ids": sorted(entity_ids),
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
                    EconomicScaleIssue(
                        code="economic_scale_portfolio_too_uniform",
                        topic_id="",
                        entity_id="",
                        path=f"/economic_scale_portfolio/{component}",
                        message="The world requires more than one population, throughput, price and scarcity band.",
                        evidence={"component": component, "values": values},
                    )
                )
        scopes = {payload.get("scale_scope", "") for payload in payloads.values()}
        if set(contracts) >= {"places", "economy_law"} and scopes != _ALLOWED_SCOPES:
            issues.append(
                EconomicScaleIssue(
                    code="economic_scale_scope_portfolio_incomplete",
                    topic_id="",
                    entity_id="",
                    path="/economic_scale_portfolio/scopes",
                    message="Scale certification requires both place populations and service systems.",
                    evidence={"scale_scopes": sorted(scopes)},
                )
            )
        service_coverage = {
            coverage_id
            for entity_id, coverage_ids in coverage_by_entity.items()
            if payloads.get(entity_id, {}).get("scale_scope") == "service_system"
            for coverage_id in coverage_ids
            if coverage_id in registry
        }
        service_count = sum(
            1
            for payload in payloads.values()
            if payload.get("scale_scope") == "service_system"
        )
        if service_count >= 2 and len(service_coverage) < 2:
            issues.append(
                EconomicScaleIssue(
                    code="economic_service_coverage_portfolio_too_narrow",
                    topic_id="",
                    entity_id="",
                    path="/economic_scale_portfolio/service_coverage",
                    message="Multiple service systems must not all represent the same single place.",
                    evidence={"affected_place_ids": sorted(service_coverage)},
                )
            )
    unique = {
        (issue.code, issue.topic_id, issue.entity_id, issue.path): issue
        for issue in issues
    }
    return tuple(unique[key] for key in sorted(unique))


def economic_scale_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    contracts = _scale_contracts(topic_graph)
    entities = _all_entities(topic_rows)
    rows = _scale_rows(entities, contracts)
    payloads = {
        entity_id: payload
        for topic_id, index, entity in rows
        if (entity_id := str(entity.get("id") or f"{topic_id}:scale:{index + 1}"))
        and (
            payload := _signature_payload(entity.get("economic_scale_signature"))[0]
        )
        is not None
    }
    issues = economic_scale_issues(topic_rows, topic_graph)
    return {
        "schema_version": "rpg_world_economic_scale_portfolio_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "checks": {
            "required_domain_count": len(contracts),
            "scale_entity_count": len(rows),
            "valid_signature_count": len(payloads),
            "unique_signature_count": len(
                {_fingerprint(payload) for payload in payloads.values()}
            ),
            "scale_scopes": sorted(
                {payload.get("scale_scope", "") for payload in payloads.values()}
            ),
            "component_diversity": {
                component: len(
                    {
                        payload.get(component, "")
                        for payload in payloads.values()
                        if payload.get(component)
                    }
                )
                for component in economic_scale_components()
            },
        },
        "entities": [
            {
                "entity_id": entity_id,
                **payload,
            }
            for entity_id, payload in sorted(payloads.items())
        ],
    }


def require_valid_economic_scale(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = economic_scale_issues(topic_rows, topic_graph)
    if issues:
        raise EconomicScaleCompilationError(issues)
    return economic_scale_report(topic_rows, topic_graph)


__all__ = [
    "EconomicScaleCompilationError",
    "EconomicScaleIssue",
    "economic_scale_issues",
    "economic_scale_report",
    "require_valid_economic_scale",
]
