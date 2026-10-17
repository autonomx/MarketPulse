"""Certification of the playable neighbouring place and deferred frontier."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .generation_starter_bubble_support import derive_starter_bubble
from .starter_bubble import (
    StarterBubblePlan,
    build_starter_map_definitions,
    predictive_materialization_queue,
)


@dataclass(frozen=True)
class StarterNeighborIssue:
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


class StarterNeighborCompilationError(ValueError):
    def __init__(self, issues: Sequence[StarterNeighborIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(f"{row.code}:{row.path}" for row in self.issues)
        super().__init__("starter_neighbor_integrity_failed:" + rendered)


def _portal_targets(definition: Any) -> set[str]:
    return {str(portal.target.map_id) for portal in definition.portals}


def _materialize(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], tuple[StarterNeighborIssue, ...]]:
    derived = derive_starter_bubble(topic_rows, topic_graph)
    enabled = bool(derived["contract_enabled"])
    empty = {
        "contract_enabled": enabled,
        "canonical_neighbor_id": str(derived["neighboring_place_id"] or ""),
        "neighbor_slot": {},
        "neighbor_map_definition": {},
        "deferred_slots": [],
        "predictive_queue": [],
    }
    if not enabled:
        return empty, ()
    plan = derived["plan"]
    if not isinstance(plan, StarterBubblePlan):
        return empty, (
            StarterNeighborIssue(
                "starter_neighbor_plan_unavailable", "/starter_neighbor",
                "Neighbour and frontier certification requires a valid starter topology plan.",
                {"canonical_neighbor_id": derived["neighboring_place_id"]},
            ),
        )
    issues: list[StarterNeighborIssue] = []
    neighbors = [slot for slot in plan.slots if slot.role == "neighbor"]
    if len(neighbors) != 1:
        issues.append(StarterNeighborIssue(
            "starter_neighbor_slot_count_invalid", "/starter_neighbor/slot",
            "The starter bubble requires exactly one neighbouring destination.",
            {"slot_count": len(neighbors)},
        ))
    neighbor = neighbors[0] if len(neighbors) == 1 else None
    canonical_neighbor = str(derived["neighboring_place_id"] or "")
    if neighbor is not None:
        if neighbor.location_id != canonical_neighbor:
            issues.append(StarterNeighborIssue(
                "starter_neighbor_identity_drift", "/starter_neighbor/slot/location_id",
                "The neighbouring slot must use the canonical connected place ID.",
                {"canonical_neighbor_id": canonical_neighbor, "materialized_neighbor_id": neighbor.location_id},
            ))
        if not neighbor.map_id or neighbor.map_level != "settlement":
            issues.append(StarterNeighborIssue(
                "starter_neighbor_map_contract_invalid", "/starter_neighbor/slot/map_id",
                "The neighbouring destination requires a settlement-level map ID.",
                {"map_id": neighbor.map_id, "map_level": neighbor.map_level},
            ))
        if neighbor.simulation_readiness != "navigable":
            issues.append(StarterNeighborIssue(
                "starter_neighbor_not_navigable", "/starter_neighbor/slot/simulation_readiness",
                "The neighbouring destination must be navigable before launch.",
                {"simulation_readiness": neighbor.simulation_readiness},
            ))
        if not bool(neighbor.metadata.get("required_before_launch")):
            issues.append(StarterNeighborIssue(
                "starter_neighbor_not_launch_required", "/starter_neighbor/slot/metadata",
                "The neighbouring destination must be launch-required.",
                {"metadata": dict(neighbor.metadata)},
            ))
        if not bool(neighbor.metadata.get("art_optional")):
            issues.append(StarterNeighborIssue(
                "starter_neighbor_optional_art_contract_missing", "/starter_neighbor/slot/metadata",
                "Neighbour navigation cannot depend on completed visual assets.",
                {"metadata": dict(neighbor.metadata)},
            ))
    definitions = build_starter_map_definitions(
        plan,
        target_world_revision=max(1, int(plan.source_world_revision)),
    )
    by_location = {
        str(definition.metadata.get("location_id") or ""): definition
        for definition in definitions
    }
    neighbor_definition = by_location.get(neighbor.location_id) if neighbor is not None else None
    if neighbor is not None and neighbor_definition is None:
        issues.append(StarterNeighborIssue(
            "starter_neighbor_map_definition_missing", "/starter_neighbor/map_definition",
            "The neighbouring destination requires an immediate navigable map definition.",
            {"neighbor_location_id": neighbor.location_id},
        ))
    if neighbor_definition is not None:
        if str(neighbor_definition.metadata.get("starter_role") or "") != "neighbor" or neighbor_definition.level != "settlement":
            issues.append(StarterNeighborIssue(
                "starter_neighbor_map_binding_invalid", "/starter_neighbor/map_definition",
                "The emitted neighbour map must preserve role and level bindings.",
                {"level": neighbor_definition.level, "metadata": dict(neighbor_definition.metadata)},
            ))
        if not neighbor_definition.definition_hash.startswith("sha256:") or not neighbor_definition.semantic_interface_hash.startswith("sha256:"):
            issues.append(StarterNeighborIssue(
                "starter_neighbor_map_hash_missing", "/starter_neighbor/map_definition",
                "The neighbour map requires stable definition and interface hashes.", {},
            ))
        if not any("arrival" in set(spawn.tags) for spawn in neighbor_definition.spawn_points):
            issues.append(StarterNeighborIssue(
                "starter_neighbor_arrival_spawn_missing", "/starter_neighbor/map_definition/spawn_points",
                "The neighbour map requires an arrival spawn.", {},
            ))
    settlement = next((slot for slot in plan.slots if slot.role == "settlement"), None)
    settlement_definition = by_location.get(settlement.location_id) if settlement is not None else None
    if settlement_definition is not None and neighbor_definition is not None:
        if neighbor_definition.map_id not in _portal_targets(settlement_definition) or settlement_definition.map_id not in _portal_targets(neighbor_definition):
            issues.append(StarterNeighborIssue(
                "starter_neighbor_portal_not_reciprocal", "/starter_neighbor/map_definition/portals",
                "Starting settlement and neighbouring destination require reciprocal portals.",
                {"settlement_map_id": settlement_definition.map_id, "neighbor_map_id": neighbor_definition.map_id},
            ))
    deferred = [slot for slot in plan.slots if slot.deferred]
    if not deferred:
        issues.append(StarterNeighborIssue(
            "starter_frontier_blueprint_required", "/starter_neighbor/deferred_slots",
            "The starter bubble requires at least one deferred frontier blueprint.", {},
        ))
    deferred_ids = {slot.location_id for slot in deferred}
    topology_deferred = {str(value) for value in plan.topology.get("deferred_location_ids") or ()}
    if deferred_ids != topology_deferred:
        issues.append(StarterNeighborIssue(
            "starter_frontier_topology_manifest_mismatch", "/starter_neighbor/topology/deferred_location_ids",
            "Topology deferred IDs must exactly match deferred starter slots.",
            {"slot_ids": sorted(deferred_ids), "topology_ids": sorted(topology_deferred)},
        ))
    immediate_locations = set(by_location)
    materialized_too_early = sorted(deferred_ids & immediate_locations)
    if materialized_too_early:
        issues.append(StarterNeighborIssue(
            "starter_frontier_materialized_too_early", "/starter_neighbor/map_definitions",
            "Deferred frontier maps must remain outside immediate launch definitions.",
            {"location_ids": materialized_too_early},
        ))
    for slot in deferred:
        if slot.simulation_readiness != "semantic" or not slot.map_id:
            issues.append(StarterNeighborIssue(
                "starter_frontier_blueprint_invalid", f"/starter_neighbor/deferred_slots/{slot.location_id}",
                "Deferred frontier slots require a semantic blueprint and future map ID.",
                {"simulation_readiness": slot.simulation_readiness, "map_id": slot.map_id},
            ))
        if bool(slot.metadata.get("required_before_launch")) or not bool(slot.metadata.get("materialize_on_approach")) or not bool(slot.metadata.get("art_optional")):
            issues.append(StarterNeighborIssue(
                "starter_frontier_deferred_policy_invalid", f"/starter_neighbor/deferred_slots/{slot.location_id}/metadata",
                "Frontiers must be non-blocking, materialize on approach, and keep art optional.",
                {"metadata": dict(slot.metadata)},
            ))
    queue = predictive_materialization_queue(
        plan,
        current_location_id=plan.starting_location_id,
    )
    queue_by_location = {str(row.get("location_id") or ""): dict(row) for row in queue}
    missing_queue = sorted(deferred_ids - set(queue_by_location))
    if missing_queue:
        issues.append(StarterNeighborIssue(
            "starter_frontier_predictive_job_missing", "/starter_neighbor/predictive_queue",
            "Every deferred frontier must have a bounded predictive materialization job.",
            {"missing_location_ids": missing_queue},
        ))
    if len(queue) > 3:
        issues.append(StarterNeighborIssue(
            "starter_frontier_predictive_queue_unbounded", "/starter_neighbor/predictive_queue",
            "The starter predictive queue must remain bounded.", {"queue_count": len(queue)},
        ))
    for location_id, row in queue_by_location.items():
        priority = row.get("priority")
        if not isinstance(priority, (int, float)) or not 0 < float(priority) <= 1:
            issues.append(StarterNeighborIssue(
                "starter_frontier_predictive_priority_invalid", f"/starter_neighbor/predictive_queue/{location_id}",
                "Predictive materialization priorities must be bounded above zero.", {"priority": priority},
            ))
        if row.get("fallback") != "navigable_placeholder" or not bool(row.get("presentation_optional")):
            issues.append(StarterNeighborIssue(
                "starter_frontier_predictive_fallback_invalid", f"/starter_neighbor/predictive_queue/{location_id}",
                "Deferred jobs require a navigable placeholder and optional presentation assets.",
                {"fallback": row.get("fallback"), "presentation_optional": row.get("presentation_optional")},
            ))
    materialization = {
        "contract_enabled": True,
        "canonical_neighbor_id": canonical_neighbor,
        "neighbor_slot": neighbor.model_dump(mode="json") if neighbor is not None else {},
        "neighbor_map_definition": neighbor_definition.model_dump(mode="json") if neighbor_definition is not None else {},
        "deferred_slots": [slot.model_dump(mode="json") for slot in deferred],
        "predictive_queue": [dict(row) for row in queue],
    }
    unique = {(row.code, row.path): row for row in issues}
    return materialization, tuple(unique[key] for key in sorted(unique))


def starter_neighbor_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    materialization, issues = _materialize(topic_rows, topic_graph)
    enabled = bool(materialization["contract_enabled"])
    return {
        "schema_version": "rpg_world_starter_neighbor_report_v1",
        "passed": not issues,
        "issues": [row.as_dict() for row in issues],
        "materialization": materialization,
        "checks": {
            "contract_enabled": enabled,
            "neighbor_materialized": not enabled or bool(materialization["neighbor_slot"]),
            "neighbor_map_materialized": not enabled or bool(materialization["neighbor_map_definition"]),
            "deferred_blueprint_count": len(materialization["deferred_slots"]),
            "predictive_job_count": len(materialization["predictive_queue"]),
        },
    }


def require_valid_starter_neighbor(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> None:
    _materialization, issues = _materialize(topic_rows, topic_graph)
    if issues:
        raise StarterNeighborCompilationError(issues)


__all__ = [
    "StarterNeighborCompilationError",
    "StarterNeighborIssue",
    "require_valid_starter_neighbor",
    "starter_neighbor_report",
]
