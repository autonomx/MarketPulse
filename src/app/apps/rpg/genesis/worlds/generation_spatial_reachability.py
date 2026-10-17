"""Depth-scaled spatial reachability and bounded travel certification."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence, cast

from app.apps.rpg.genesis.forge.world_forge_spatial_routes import (
    minimum_route_count,
    spatial_route_components,
)

_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FORBIDDEN_VALUES = {
    "travel_time_band": {
        "instant",
        "zero_time",
        "none",
        "teleport",
        "immediate_everywhere",
    },
    "access_mode": {
        "teleportation",
        "instant_transfer",
        "unrestricted_everywhere",
    },
    "route_blocker": {
        "none",
        "unrestricted",
        "always_open",
        "no_blocker",
    },
    "failure_condition": {
        "none",
        "infallible",
        "cannot_fail",
        "no_failure",
    },
}
_DIVERSITY_COMPONENTS = (
    "travel_time_band",
    "access_mode",
    "route_blocker",
    "failure_condition",
)


@dataclass(frozen=True)
class SpatialReachabilityIssue:
    code: str
    topic_id: str
    place_id: str
    path: str
    message: str
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "topic_id": self.topic_id,
            "place_id": self.place_id,
            "path": self.path,
            "message": self.message,
            "evidence": dict(self.evidence),
            "severity": "error",
            "blocking": True,
        }


class SpatialReachabilityCompilationError(ValueError):
    def __init__(self, issues: Sequence[SpatialReachabilityIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.topic_id}:{issue.place_id}:{issue.path}"
            for issue in self.issues
        )
        super().__init__("spatial_reachability_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "spatial_reachability_failed",
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


def _spatial_domains(topic_graph: Mapping[str, Any] | None) -> set[str]:
    graph = _mapping(topic_graph)
    domains: set[str] = set()
    for node in _rows(graph.get("nodes")):
        topic_id = str(node.get("topic_id") or "")
        contract = _mapping(
            _mapping(node.get("metadata")).get("spatial_route_contract")
        )
        if topic_id and bool(contract.get("required")):
            domains.add(topic_id)
    contract = _mapping(
        _mapping(graph.get("metadata")).get("spatial_route_contract")
    )
    domains.update(
        str(value) for value in contract.get("domain_ids") or () if str(value)
    )
    return domains


def _graph_contract(topic_graph: Mapping[str, Any] | None) -> dict[str, Any]:
    return _mapping(
        _mapping(topic_graph).get("metadata", {}).get("spatial_route_contract")
    )


def _normalise_category(value: Any) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _signature_payload(value: Any) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return None, ("travel_route_signature_must_be_object",)
    row = dict(value)
    payload: dict[str, str] = {}
    issues: list[str] = []
    for component in spatial_route_components():
        normalised = _normalise_category(row.get(component))
        if not normalised:
            issues.append(f"travel_route_component_required:{component}")
            continue
        if not _CATEGORY.fullmatch(normalised):
            issues.append(f"travel_route_component_invalid:{component}")
            continue
        payload[component] = normalised
    return (payload if not issues else None), tuple(issues)


def _id_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


def _place_rows(
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
            if topic_id in required_domains or "travel_route_signature" in entity:
                values.append((topic_id, index, entity))
    return tuple(values)


def _connected_components(
    place_ids: set[str],
    edges: set[tuple[str, str]],
) -> tuple[tuple[str, ...], ...]:
    adjacency: dict[str, set[str]] = {place_id: set() for place_id in place_ids}
    for source_id, target_id in edges:
        adjacency[source_id].add(target_id)
        adjacency[target_id].add(source_id)
    pending = set(place_ids)
    components: list[tuple[str, ...]] = []
    while pending:
        root = min(pending)
        seen = {root}
        frontier = [root]
        while frontier:
            current = frontier.pop()
            for target_id in sorted(adjacency[current]):
                if target_id not in seen:
                    seen.add(target_id)
                    frontier.append(target_id)
        pending.difference_update(seen)
        components.append(tuple(sorted(seen)))
    return tuple(sorted(components, key=lambda value: (value[0] if value else "", value)))


def spatial_reachability_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[SpatialReachabilityIssue, ...]:
    required_domains = _spatial_domains(topic_graph)
    rows = _place_rows(topic_rows, required_domains)
    place_ids = {
        str(place.get("id") or "")
        for _topic_id, _index, place in rows
        if str(place.get("id") or "")
    }
    issues: list[SpatialReachabilityIssue] = []
    edges: set[tuple[str, str]] = set()
    signatures: dict[str, dict[str, str]] = {}
    for topic_id, index, place in rows:
        place_id = str(place.get("id") or f"{topic_id}:place:{index + 1}")
        path = f"/{topic_id}/entities/{index}"
        connected_ids = _id_list(place.get("connected_place_ids"))
        valid_targets: list[str] = []
        if not connected_ids:
            issues.append(
                SpatialReachabilityIssue(
                    code="place_connection_required",
                    topic_id=topic_id,
                    place_id=place_id,
                    path=f"{path}/connected_place_ids",
                    message="Every contracted place requires at least one canonical route endpoint.",
                    evidence={"connected_place_ids": []},
                )
            )
        for target_id in connected_ids:
            if target_id == place_id:
                issues.append(
                    SpatialReachabilityIssue(
                        code="spatial_route_self_reference",
                        topic_id=topic_id,
                        place_id=place_id,
                        path=f"{path}/connected_place_ids",
                        message="A place cannot use itself as a travel route endpoint.",
                        evidence={"target_place_id": target_id},
                    )
                )
                continue
            if target_id not in place_ids:
                issues.append(
                    SpatialReachabilityIssue(
                        code="spatial_route_target_unknown",
                        topic_id=topic_id,
                        place_id=place_id,
                        path=f"{path}/connected_place_ids",
                        message="Travel route endpoints must resolve to canonical places.",
                        evidence={"target_place_id": target_id},
                    )
                )
                continue
            valid_targets.append(target_id)
            edges.add(cast(tuple[str, str], tuple(sorted((place_id, target_id)))))
        signature = place.get("travel_route_signature")
        if signature is None:
            issues.append(
                SpatialReachabilityIssue(
                    code="travel_route_signature_required",
                    topic_id=topic_id,
                    place_id=place_id,
                    path=f"{path}/travel_route_signature",
                    message="Every contracted place requires bounded travel constraints.",
                    evidence={"valid_target_ids": sorted(valid_targets)},
                )
            )
            continue
        payload, signature_issues = _signature_payload(signature)
        for code in signature_issues:
            issues.append(
                SpatialReachabilityIssue(
                    code=code,
                    topic_id=topic_id,
                    place_id=place_id,
                    path=f"{path}/travel_route_signature",
                    message="Travel route signature does not satisfy the categorical contract.",
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
        signatures[place_id] = payload
        for component, forbidden_values in _FORBIDDEN_VALUES.items():
            value = payload.get(component, "")
            if value in forbidden_values:
                issues.append(
                    SpatialReachabilityIssue(
                        code="unbounded_spatial_route",
                        topic_id=topic_id,
                        place_id=place_id,
                        path=f"{path}/travel_route_signature/{component}",
                        message=(
                            "Routes must expose non-zero travel, bounded access, "
                            "blockers, and concrete failure conditions."
                        ),
                        evidence={"component": component, "value": value},
                    )
                )

    if len(place_ids) >= 2:
        components = _connected_components(place_ids, edges)
        if len(components) != 1:
            issues.append(
                SpatialReachabilityIssue(
                    code="spatial_graph_disconnected",
                    topic_id="",
                    place_id="",
                    path="/spatial_route_portfolio/components",
                    message="All campaign places must be reachable through explicit routes.",
                    evidence={"components": [list(value) for value in components]},
                )
            )
        contract = _graph_contract(topic_graph)
        depth = str(contract.get("depth") or _mapping(topic_graph).get("depth") or "standard")
        required_routes = int(
            contract.get("minimum_route_count")
            or minimum_route_count(len(place_ids), depth)
        )
        if len(edges) < required_routes:
            issues.append(
                SpatialReachabilityIssue(
                    code="spatial_route_count_below_depth_floor",
                    topic_id="",
                    place_id="",
                    path="/spatial_route_portfolio/routes",
                    message="The route graph is too sparse for the selected campaign depth.",
                    evidence={
                        "depth": depth,
                        "route_count": len(edges),
                        "minimum_route_count": required_routes,
                        "routes": [list(value) for value in sorted(edges)],
                    },
                )
            )
    if len(place_ids) >= 5:
        for component in _DIVERSITY_COMPONENTS:
            values = sorted(
                {
                    payload.get(component, "")
                    for payload in signatures.values()
                    if payload.get(component)
                }
            )
            if len(values) < 2:
                issues.append(
                    SpatialReachabilityIssue(
                        code="spatial_route_portfolio_too_uniform",
                        topic_id="",
                        place_id="",
                        path=f"/spatial_route_portfolio/{component}",
                        message="Larger worlds require varied travel times, modes, blockers, and failures.",
                        evidence={"component": component, "values": values},
                    )
                )
    unique = {
        (issue.code, issue.topic_id, issue.place_id, issue.path): issue
        for issue in issues
    }
    return tuple(unique[key] for key in sorted(unique))


def spatial_reachability_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    required_domains = _spatial_domains(topic_graph)
    rows = _place_rows(topic_rows, required_domains)
    place_ids = {
        str(place.get("id") or "")
        for _topic, _index, place in rows
        if str(place.get("id") or "")
    }
    edges = {
        tuple(sorted((source_id, target_id)))
        for _topic, _index, place in rows
        if (source_id := str(place.get("id") or ""))
        for target_id in _id_list(place.get("connected_place_ids"))
        if target_id in place_ids and target_id != source_id
    }
    signatures = {
        place_id: payload
        for _topic, index, place in rows
        if (place_id := str(place.get("id") or f"place:{index + 1}"))
        and (
            payload := _signature_payload(place.get("travel_route_signature"))[0]
        )
        is not None
    }
    contract = _graph_contract(topic_graph)
    depth = str(contract.get("depth") or _mapping(topic_graph).get("depth") or "standard")
    required_routes = int(
        contract.get("minimum_route_count")
        or minimum_route_count(len(place_ids), depth)
    )
    components = _connected_components(place_ids, cast(Any, edges)) if place_ids else ()
    issues = spatial_reachability_issues(topic_rows, topic_graph)
    return {
        "schema_version": "rpg_world_spatial_reachability_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "checks": {
            "required_domain_count": len(required_domains),
            "place_count": len(place_ids),
            "route_count": len(edges),
            "minimum_route_count": required_routes,
            "connected_component_count": len(components),
            "valid_signature_count": len(signatures),
            "component_diversity": {
                component: len(
                    {
                        payload.get(component, "")
                        for payload in signatures.values()
                        if payload.get(component)
                    }
                )
                for component in spatial_route_components()
            },
        },
        "routes": [
            {"source_place_id": source_id, "target_place_id": target_id}
            for source_id, target_id in sorted(edges)
        ],
        "components": [list(value) for value in components],
    }


def require_spatial_reachability(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = spatial_reachability_issues(topic_rows, topic_graph)
    if issues:
        raise SpatialReachabilityCompilationError(issues)
    return spatial_reachability_report(topic_rows, topic_graph)


__all__ = [
    "SpatialReachabilityCompilationError",
    "SpatialReachabilityIssue",
    "require_spatial_reachability",
    "spatial_reachability_issues",
    "spatial_reachability_report",
]
