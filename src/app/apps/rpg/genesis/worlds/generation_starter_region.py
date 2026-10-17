"""Certification of the canonical starting region in a generated starter bubble."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .generation_starter_bubble_support import derive_starter_bubble
from .starter_bubble import StarterBubblePlan


@dataclass(frozen=True)
class StarterRegionIssue:
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


class StarterRegionCompilationError(ValueError):
    def __init__(self, issues: Sequence[StarterRegionIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(f"{row.code}:{row.path}" for row in self.issues)
        super().__init__("starter_region_integrity_failed:" + rendered)


def starter_region_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[StarterRegionIssue, ...]:
    derived = derive_starter_bubble(topic_rows, topic_graph)
    if not derived["contract_enabled"]:
        return ()
    issues: list[StarterRegionIssue] = []
    region_id = str(derived["starting_region_id"] or "")
    canonical_regions = set(derived["region_ids"])
    if not region_id or region_id not in canonical_regions:
        issues.append(StarterRegionIssue(
            "starter_region_reference_unresolved", "/starter_region/region_id",
            "The starting place must reference one canonical generated region.",
            {"starting_region_id": region_id, "canonical_region_ids": sorted(canonical_regions)},
        ))
    plan = derived["plan"]
    if isinstance(plan, StarterBubblePlan):
        regions = [slot for slot in plan.slots if slot.role == "region"]
        if len(regions) != 1:
            issues.append(StarterRegionIssue(
                "starter_region_slot_count_invalid", "/starter_region/slots",
                "The starter bubble must materialize exactly one region slot.",
                {"region_slot_count": len(regions)},
            ))
        else:
            slot = regions[0]
            if region_id and slot.location_id != region_id:
                issues.append(StarterRegionIssue(
                    "starter_region_identity_drift", "/starter_region/slots/0/location_id",
                    "The materialized region ID must equal the starting place's canonical region ID.",
                    {"canonical_region_id": region_id, "materialized_region_id": slot.location_id},
                ))
            if not bool(slot.metadata.get("owns_world_graph")):
                issues.append(StarterRegionIssue(
                    "starter_region_topology_ownership_required", "/starter_region/slots/0/metadata",
                    "The starting region must own the starter world graph.",
                    {"metadata": dict(slot.metadata)},
                ))
            if plan.starting_location_id not in slot.connected_location_ids:
                issues.append(StarterRegionIssue(
                    "starter_region_starting_settlement_not_connected", "/starter_region/slots/0/connected_location_ids",
                    "The canonical starting settlement must be connected to its region slot.",
                    {"starting_location_id": plan.starting_location_id, "connected_location_ids": list(slot.connected_location_ids)},
                ))
        if region_id and str(plan.topology.get("region_id") or "") != region_id:
            issues.append(StarterRegionIssue(
                "starter_region_topology_identity_drift", "/starter_region/topology/region_id",
                "Topology region identity must match canonical region identity.",
                {"canonical_region_id": region_id, "topology_region_id": plan.topology.get("region_id")},
            ))
    unique = {(row.code, row.path): row for row in issues}
    return tuple(unique[key] for key in sorted(unique))


def starter_region_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    derived = derive_starter_bubble(topic_rows, topic_graph)
    issues = starter_region_issues(topic_rows, topic_graph)
    plan = derived["plan"]
    region_slot = next(
        (slot.model_dump(mode="json") for slot in plan.slots if slot.role == "region"),
        {},
    ) if isinstance(plan, StarterBubblePlan) else {}
    enabled = bool(derived["contract_enabled"])
    return {
        "schema_version": "rpg_world_starter_region_report_v1",
        "passed": not issues,
        "issues": [row.as_dict() for row in issues],
        "materialization": {
            "contract_enabled": enabled,
            "starting_place_id": str(derived["starting_place_id"] or ""),
            "canonical_region_id": str(derived["starting_region_id"] or ""),
            "region_slot": region_slot,
        },
        "checks": {
            "contract_enabled": enabled,
            "canonical_region_resolved": not enabled or bool(derived["starting_region_id"]),
            "single_region_slot": not enabled or bool(region_slot),
            "topology_owned_by_region": not enabled or bool(region_slot.get("metadata", {}).get("owns_world_graph")),
        },
    }


def require_valid_starter_region(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> None:
    issues = starter_region_issues(topic_rows, topic_graph)
    if issues:
        raise StarterRegionCompilationError(issues)


__all__ = [
    "StarterRegionCompilationError",
    "StarterRegionIssue",
    "require_valid_starter_region",
    "starter_region_issues",
    "starter_region_report",
]
