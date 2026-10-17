"""Artifact-level certification for the neighbour map and deferred frontier queue."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.world.map_grid_contracts import with_grid_definition_hashes

from .generation_starter_bubble_support import derive_starter_bubble
from .starter_bubble import (
    StarterBubblePlan,
    build_starter_map_definitions,
    predictive_materialization_queue,
)


@dataclass(frozen=True)
class StarterNeighborArtifactIssue:
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


class StarterNeighborArtifactCompilationError(ValueError):
    def __init__(self, issues: Sequence[StarterNeighborArtifactIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(f"{row.code}:{row.path}" for row in self.issues)
        super().__init__("starter_neighbor_artifact_integrity_failed:" + rendered)


def _open_portal(source: Any, target_map_id: str) -> bool:
    return any(
        portal.source.map_id == source.map_id
        and portal.target.map_id == target_map_id
        and portal.state == "open"
        for portal in source.portals
    )


def _audit(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], tuple[StarterNeighborArtifactIssue, ...]]:
    derived = derive_starter_bubble(topic_rows, topic_graph)
    enabled = bool(derived["contract_enabled"])
    empty = {
        "contract_enabled": enabled,
        "neighbor_map_id": "",
        "deferred_location_ids": [],
        "predictive_queue": [],
    }
    if not enabled:
        return empty, ()
    plan = derived["plan"]
    if not isinstance(plan, StarterBubblePlan):
        issue = StarterNeighborArtifactIssue(
            "starter_neighbor_artifact_plan_unavailable",
            "/starter_neighbor_artifacts",
            "Neighbour artifacts require a valid starter topology plan.",
            {},
        )
        return empty, (issue,)
    issues: list[StarterNeighborArtifactIssue] = []

    def add(code: str, path: str, message: str, evidence: Mapping[str, Any]) -> None:
        issues.append(StarterNeighborArtifactIssue(code, path, message, evidence))

    definitions = build_starter_map_definitions(
        plan,
        target_world_revision=max(1, int(plan.source_world_revision)),
    )
    definition_locations = [
        str(definition.metadata.get("location_id") or "")
        for definition in definitions
    ]
    definition_map_ids = [definition.map_id for definition in definitions]
    if len(definition_locations) != len(set(definition_locations)) or len(
        definition_map_ids
    ) != len(set(definition_map_ids)):
        add(
            "starter_neighbor_immediate_definition_duplicate",
            "/starter_neighbor_artifacts/map_definitions",
            "Immediate starter definitions require unique location and map IDs.",
            {
                "location_ids": definition_locations,
                "map_ids": definition_map_ids,
            },
        )
    by_location = {
        location_id: definition
        for location_id, definition in zip(definition_locations, definitions)
    }
    neighbor = next((slot for slot in plan.slots if slot.role == "neighbor"), None)
    neighbor_definition = by_location.get(neighbor.location_id) if neighbor else None
    if neighbor is not None and neighbor_definition is not None:
        role = str(neighbor_definition.metadata.get("starter_role") or "")
        location_id = str(neighbor_definition.metadata.get("location_id") or "")
        if (
            neighbor_definition.map_id != neighbor.map_id
            or neighbor_definition.level != neighbor.map_level
            or role != neighbor.role
            or location_id != neighbor.location_id
        ):
            add(
                "starter_neighbor_artifact_binding_invalid",
                "/starter_neighbor_artifacts/neighbor_map",
                "The neighbour map must preserve map ID, level, role, and canonical place ID.",
                {
                    "expected_map_id": neighbor.map_id,
                    "actual_map_id": neighbor_definition.map_id,
                    "expected_level": neighbor.map_level,
                    "actual_level": neighbor_definition.level,
                    "expected_role": neighbor.role,
                    "actual_role": role,
                    "expected_location_id": neighbor.location_id,
                    "actual_location_id": location_id,
                },
            )
        rehashed = with_grid_definition_hashes(neighbor_definition)
        if (
            neighbor_definition.definition_hash != rehashed.definition_hash
            or neighbor_definition.semantic_interface_hash
            != rehashed.semantic_interface_hash
        ):
            add(
                "starter_neighbor_artifact_hash_invalid",
                "/starter_neighbor_artifacts/neighbor_map",
                "Neighbour hashes must exactly match deterministic map and interface content.",
                {
                    "definition_hash": neighbor_definition.definition_hash,
                    "expected_definition_hash": rehashed.definition_hash,
                    "semantic_interface_hash": neighbor_definition.semantic_interface_hash,
                    "expected_semantic_interface_hash": rehashed.semantic_interface_hash,
                },
            )
    settlement = next((slot for slot in plan.slots if slot.role == "settlement"), None)
    settlement_definition = by_location.get(settlement.location_id) if settlement else None
    if settlement_definition is not None and neighbor_definition is not None:
        if not (
            _open_portal(settlement_definition, neighbor_definition.map_id)
            and _open_portal(neighbor_definition, settlement_definition.map_id)
        ):
            add(
                "starter_neighbor_artifact_portal_invalid",
                "/starter_neighbor_artifacts/portals",
                "Settlement and neighbour definitions require reciprocal open portals.",
                {
                    "settlement_map_id": settlement_definition.map_id,
                    "neighbor_map_id": neighbor_definition.map_id,
                },
            )

    deferred = [slot for slot in plan.slots if slot.deferred]
    deferred_ids = {slot.location_id for slot in deferred}
    deferred_map_ids = {str(slot.map_id) for slot in deferred if slot.map_id}
    early_locations = sorted(deferred_ids.intersection(definition_locations))
    early_maps = sorted(deferred_map_ids.intersection(definition_map_ids))
    if early_locations or early_maps:
        add(
            "starter_frontier_artifact_materialized_early",
            "/starter_neighbor_artifacts/map_definitions",
            "Deferred frontier blueprints must remain outside immediate map definitions.",
            {"location_ids": early_locations, "map_ids": early_maps},
        )
    for slot in deferred:
        if slot.role != "frontier" or slot.map_level != "encounter":
            add(
                "starter_frontier_artifact_blueprint_invalid",
                f"/starter_neighbor_artifacts/deferred/{slot.location_id}",
                "Deferred starter slots must be explicit encounter-level frontier blueprints.",
                {"role": slot.role, "map_level": slot.map_level},
            )

    queue = [
        dict(row)
        for row in predictive_materialization_queue(
            plan,
            current_location_id=plan.starting_location_id,
        )
    ]
    queue_ids = [str(row.get("location_id") or "") for row in queue]
    if len(queue_ids) != len(set(queue_ids)):
        add(
            "starter_frontier_artifact_queue_duplicate",
            "/starter_neighbor_artifacts/predictive_queue",
            "Predictive frontier jobs require unique location IDs.",
            {"location_ids": queue_ids},
        )
    missing = sorted(deferred_ids - set(queue_ids))
    unexpected = sorted(set(queue_ids) - deferred_ids)
    if missing or unexpected or len(queue) > 3:
        add(
            "starter_frontier_artifact_queue_scope_invalid",
            "/starter_neighbor_artifacts/predictive_queue",
            "The bounded predictive queue must exactly cover declared deferred frontiers.",
            {
                "missing_location_ids": missing,
                "unexpected_location_ids": unexpected,
                "queue_count": len(queue),
            },
        )
    ordered = sorted(
        queue,
        key=lambda row: (
            -float(row.get("priority") or 0),
            str(row.get("location_id") or ""),
        ),
    )
    if queue != ordered:
        add(
            "starter_frontier_artifact_queue_order_invalid",
            "/starter_neighbor_artifacts/predictive_queue",
            "Predictive jobs require deterministic priority and location ordering.",
            {"location_ids": queue_ids},
        )
    deferred_by_id = {slot.location_id: slot for slot in deferred}
    for row in queue:
        location_id = str(row.get("location_id") or "")
        deferred_slot = deferred_by_id.get(location_id)
        if deferred_slot is not None and row.get("map_id") != deferred_slot.map_id:
            add(
                "starter_frontier_artifact_queue_binding_invalid",
                f"/starter_neighbor_artifacts/predictive_queue/{location_id}",
                "Predictive jobs must preserve deferred blueprint map IDs.",
                {"expected_map_id": deferred_slot.map_id, "actual_map_id": row.get("map_id")},
            )
        if (
            row.get("resource_class") != "cpu"
            or row.get("fallback") != "navigable_placeholder"
            or not bool(row.get("presentation_optional"))
        ):
            add(
                "starter_frontier_artifact_fallback_invalid",
                f"/starter_neighbor_artifacts/predictive_queue/{location_id}",
                "Deferred jobs require bounded CPU work, navigable fallback, and optional presentation.",
                {
                    "resource_class": row.get("resource_class"),
                    "fallback": row.get("fallback"),
                    "presentation_optional": row.get("presentation_optional"),
                },
            )
    materialization = {
        "contract_enabled": True,
        "neighbor_map_id": neighbor_definition.map_id if neighbor_definition else "",
        "deferred_location_ids": sorted(deferred_ids),
        "predictive_queue": queue,
    }
    unique = {(row.code, row.path): row for row in issues}
    return materialization, tuple(unique[key] for key in sorted(unique))


def starter_neighbor_artifact_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    materialization, issues = _audit(topic_rows, topic_graph)
    return {
        "schema_version": "rpg_world_starter_neighbor_artifact_report_v1",
        "passed": not issues,
        "issues": [row.as_dict() for row in issues],
        "materialization": materialization,
    }


def require_valid_starter_neighbor_artifacts(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> None:
    _materialization, issues = _audit(topic_rows, topic_graph)
    if issues:
        raise StarterNeighborArtifactCompilationError(issues)


__all__ = [
    "StarterNeighborArtifactCompilationError",
    "StarterNeighborArtifactIssue",
    "require_valid_starter_neighbor_artifacts",
    "starter_neighbor_artifact_report",
]
