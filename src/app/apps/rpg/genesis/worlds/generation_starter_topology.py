"""Certification of canonical Release 6 starter topology."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .generation_starter_bubble_support import derive_starter_bubble
from .starter_bubble import StarterBubblePlan


@dataclass(frozen=True)
class StarterTopologyIssue:
    code: str
    path: str
    message: str
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "evidence": dict(self.evidence),
            "severity": "error",
            "blocking": True,
        }


class StarterTopologyCompilationError(ValueError):
    def __init__(self, issues: Sequence[StarterTopologyIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(f"{row.code}:{row.path}" for row in self.issues)
        super().__init__("starter_topology_integrity_failed:" + rendered)


def _plan_issues(plan: StarterBubblePlan) -> tuple[StarterTopologyIssue, ...]:
    issues: list[StarterTopologyIssue] = []
    slot_ids = [slot.location_id for slot in plan.slots]
    locations = [str(value) for value in plan.topology.get("locations") or ()]
    if len(slot_ids) != len(set(slot_ids)):
        issues.append(StarterTopologyIssue(
            "starter_topology_location_id_duplicate", "/starter_topology/locations",
            "Starter location IDs must be unique.", {"location_ids": slot_ids},
        ))
    if set(locations) != set(slot_ids) or len(locations) != len(slot_ids):
        issues.append(StarterTopologyIssue(
            "starter_topology_location_manifest_mismatch", "/starter_topology/locations",
            "Topology locations must exactly match the planned starter slots.",
            {"topology_location_ids": locations, "slot_location_ids": slot_ids},
        ))
    route_rows = [dict(row) for row in plan.topology.get("routes") or () if isinstance(row, Mapping)]
    route_ids = [str(row.get("route_id") or "") for row in route_rows]
    if not route_ids or len(route_ids) != len(set(route_ids)) or any(not value for value in route_ids):
        issues.append(StarterTopologyIssue(
            "starter_topology_route_id_invalid", "/starter_topology/routes",
            "Starter route IDs must be present and unique.", {"route_ids": route_ids},
        ))
    known = set(slot_ids)
    adjacency: dict[str, set[str]] = {location_id: set() for location_id in known}
    invalid_endpoints: set[str] = set()
    for row in route_rows:
        source = str(row.get("source_location_id") or "")
        target = str(row.get("target_location_id") or "")
        if source not in known:
            invalid_endpoints.add(source)
        if target not in known:
            invalid_endpoints.add(target)
        if source in known and target in known and source != target:
            adjacency[source].add(target)
            adjacency[target].add(source)
    for slot in plan.slots:
        for target in slot.connected_location_ids:
            if target not in known:
                invalid_endpoints.add(target)
            elif target != slot.location_id:
                adjacency[slot.location_id].add(target)
                adjacency[target].add(slot.location_id)
    invalid_endpoints.discard("")
    if invalid_endpoints:
        issues.append(StarterTopologyIssue(
            "starter_topology_endpoint_unresolved", "/starter_topology/routes",
            "Every route and slot connection must resolve inside the starter topology.",
            {"invalid_location_ids": sorted(invalid_endpoints)},
        ))
    seen: set[str] = set()
    pending = [plan.starting_location_id] if plan.starting_location_id in known else []
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(adjacency.get(current, set()) - seen)
    if seen != known:
        issues.append(StarterTopologyIssue(
            "starter_topology_disconnected", "/starter_topology/routes",
            "Every starter slot must be reachable from the starting settlement.",
            {"unreachable_location_ids": sorted(known - seen)},
        ))
    return tuple(issues)


def starter_topology_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[StarterTopologyIssue, ...]:
    derived = derive_starter_bubble(topic_rows, topic_graph)
    if not derived["contract_enabled"]:
        return ()
    issues: list[StarterTopologyIssue] = []
    start = str(derived["starting_place_id"] or "")
    neighbor = str(derived["neighboring_place_id"] or "")
    canonical = set(derived["place_ids"])
    if not start or start not in canonical:
        issues.append(StarterTopologyIssue(
            "starter_topology_starting_place_unresolved", "/starter_topology/starting_place_id",
            "The starter topology requires one canonical starting place.",
            {"starting_place_id": start, "canonical_place_ids": sorted(canonical)},
        ))
    if not neighbor or neighbor not in canonical or neighbor == start:
        issues.append(StarterTopologyIssue(
            "starter_topology_neighbor_unresolved", "/starter_topology/neighboring_place_id",
            "The starter topology requires a distinct canonical place connected to the start.",
            {"neighboring_place_id": neighbor, "starting_place_id": start},
        ))
    plan = derived["plan"]
    if isinstance(plan, StarterBubblePlan):
        issues.extend(_plan_issues(plan))
    unique = {(row.code, row.path): row for row in issues}
    return tuple(unique[key] for key in sorted(unique))


def starter_topology_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    derived = derive_starter_bubble(topic_rows, topic_graph)
    issues = starter_topology_issues(topic_rows, topic_graph)
    plan = derived["plan"]
    enabled = bool(derived["contract_enabled"])
    materialization = (
        plan.model_dump(mode="json") if isinstance(plan, StarterBubblePlan) else {
            "schema_version": "rpg_starter_bubble_v1",
            "starting_location_id": str(derived["starting_place_id"] or ""),
            "slots": [],
            "topology": {},
        }
    )
    return {
        "schema_version": "rpg_world_starter_topology_report_v1",
        "passed": not issues,
        "issues": [row.as_dict() for row in issues],
        "materialization": materialization,
        "checks": {
            "contract_enabled": enabled,
            "skipped_when_not_declared": enabled or not enabled,
            "canonical_starting_place": not enabled or bool(derived["starting_place_id"]),
            "canonical_neighboring_place": not enabled or bool(derived["neighboring_place_id"]),
            "location_count": len(plan.slots) if isinstance(plan, StarterBubblePlan) else 0,
            "route_count": len(plan.topology.get("routes") or ()) if isinstance(plan, StarterBubblePlan) else 0,
        },
    }


def require_valid_starter_topology(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> None:
    issues = starter_topology_issues(topic_rows, topic_graph)
    if issues:
        raise StarterTopologyCompilationError(issues)


__all__ = [
    "StarterTopologyCompilationError",
    "StarterTopologyIssue",
    "require_valid_starter_topology",
    "starter_topology_issues",
    "starter_topology_report",
]
