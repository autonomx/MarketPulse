"""Certification of the navigable starting settlement and interior."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.world.map_grid_contracts import with_grid_definition_hashes

from .generation_starter_bubble_support import derive_starter_bubble
from .starter_bubble import StarterBubblePlan, build_starter_map_definitions


@dataclass(frozen=True)
class StarterCoreLocationIssue:
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


class StarterCoreLocationCompilationError(ValueError):
    def __init__(self, issues: Sequence[StarterCoreLocationIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(f"{row.code}:{row.path}" for row in self.issues)
        super().__init__("starter_core_location_integrity_failed:" + rendered)


def _portal_targets(definition: Any) -> set[str]:
    return {str(portal.target.map_id) for portal in definition.portals}


def _has_arrival_spawn(definition: Any) -> bool:
    return any("arrival" in set(spawn.tags) for spawn in definition.spawn_points)


def _core_materialization(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], tuple[StarterCoreLocationIssue, ...]]:
    derived = derive_starter_bubble(topic_rows, topic_graph)
    enabled = bool(derived["contract_enabled"])
    empty = {
        "contract_enabled": enabled,
        "starting_place_id": str(derived["starting_place_id"] or ""),
        "settlement_slot": {},
        "interior_slot": {},
        "map_definitions": [],
    }
    if not enabled:
        return empty, ()
    plan = derived["plan"]
    if not isinstance(plan, StarterBubblePlan):
        return empty, (
            StarterCoreLocationIssue(
                "starter_core_plan_unavailable", "/starter_core",
                "Settlement and interior maps require a valid starter topology plan.",
                {"starting_place_id": derived["starting_place_id"]},
            ),
        )
    issues: list[StarterCoreLocationIssue] = []
    settlements = [slot for slot in plan.slots if slot.role == "settlement"]
    interiors = [slot for slot in plan.slots if slot.role == "interior"]
    if len(settlements) != 1:
        issues.append(StarterCoreLocationIssue(
            "starter_settlement_slot_count_invalid", "/starter_core/settlement",
            "The starter bubble requires exactly one settlement slot.",
            {"slot_count": len(settlements)},
        ))
    if len(interiors) != 1:
        issues.append(StarterCoreLocationIssue(
            "starter_interior_slot_count_invalid", "/starter_core/interior",
            "The starter bubble requires exactly one interior slot.",
            {"slot_count": len(interiors)},
        ))
    settlement = settlements[0] if len(settlements) == 1 else None
    interior = interiors[0] if len(interiors) == 1 else None
    if settlement is not None:
        if settlement.location_id != plan.starting_location_id:
            issues.append(StarterCoreLocationIssue(
                "starter_settlement_identity_drift", "/starter_core/settlement/location_id",
                "The settlement slot must use the canonical starting place ID.",
                {"starting_place_id": plan.starting_location_id, "settlement_location_id": settlement.location_id},
            ))
        if settlement.map_level != "settlement" or not settlement.map_id:
            issues.append(StarterCoreLocationIssue(
                "starter_settlement_map_contract_invalid", "/starter_core/settlement/map_id",
                "The settlement requires a settlement-level map ID.",
                {"map_id": settlement.map_id, "map_level": settlement.map_level},
            ))
    if interior is not None:
        if interior.map_level != "interior" or not interior.map_id:
            issues.append(StarterCoreLocationIssue(
                "starter_interior_map_contract_invalid", "/starter_core/interior/map_id",
                "The interior requires an interior-level map ID.",
                {"map_id": interior.map_id, "map_level": interior.map_level},
            ))
        if settlement is not None and settlement.location_id not in interior.connected_location_ids:
            issues.append(StarterCoreLocationIssue(
                "starter_interior_not_connected_to_settlement", "/starter_core/interior/connected_location_ids",
                "The starting interior must connect back to the starting settlement.",
                {"settlement_location_id": settlement.location_id, "connected_location_ids": list(interior.connected_location_ids)},
            ))
    for slot in (settlement, interior):
        if slot is None:
            continue
        if slot.simulation_readiness != "navigable":
            issues.append(StarterCoreLocationIssue(
                "starter_core_slot_not_navigable", f"/starter_core/{slot.role}/simulation_readiness",
                "Starting settlement and interior slots must be navigable before launch.",
                {"role": slot.role, "simulation_readiness": slot.simulation_readiness},
            ))
        if not bool(slot.metadata.get("required_before_launch")):
            issues.append(StarterCoreLocationIssue(
                "starter_core_slot_not_launch_required", f"/starter_core/{slot.role}/metadata",
                "Starting settlement and interior must be required before launch.",
                {"role": slot.role, "metadata": dict(slot.metadata)},
            ))
    definitions = build_starter_map_definitions(
        plan,
        target_world_revision=max(1, int(plan.source_world_revision)),
    )
    by_location = {
        str(definition.metadata.get("location_id") or ""): definition
        for definition in definitions
    }
    expected_slots = {
        slot.location_id: slot
        for slot in (settlement, interior)
        if slot is not None
    }
    core_ids = set(expected_slots)
    missing = sorted(core_ids - set(by_location))
    if missing:
        issues.append(StarterCoreLocationIssue(
            "starter_core_map_definition_missing", "/starter_core/map_definitions",
            "Both settlement and interior require materialized map definitions.",
            {"missing_location_ids": missing},
        ))
    core_definitions = [by_location[value] for value in sorted(core_ids & set(by_location))]
    map_ids = [definition.map_id for definition in core_definitions]
    if len(map_ids) != len(set(map_ids)):
        issues.append(StarterCoreLocationIssue(
            "starter_core_map_id_duplicate", "/starter_core/map_definitions",
            "Settlement and interior map IDs must be unique.", {"map_ids": map_ids},
        ))
    for definition in core_definitions:
        location_id = str(definition.metadata.get("location_id") or "")
        slot = expected_slots[location_id]
        role = str(definition.metadata.get("starter_role") or "")
        if definition.map_id != slot.map_id or definition.level != slot.map_level or role != slot.role:
            issues.append(StarterCoreLocationIssue(
                "starter_core_map_binding_invalid",
                f"/starter_core/map_definitions/{definition.map_id}",
                "Each emitted map must preserve its slot map ID, level, role, and canonical location binding.",
                {
                    "location_id": location_id,
                    "expected_map_id": slot.map_id,
                    "actual_map_id": definition.map_id,
                    "expected_level": slot.map_level,
                    "actual_level": definition.level,
                    "expected_role": slot.role,
                    "actual_role": role,
                },
            ))
        rehashed = with_grid_definition_hashes(definition)
        if (
            definition.definition_hash != rehashed.definition_hash
            or definition.semantic_interface_hash != rehashed.semantic_interface_hash
        ):
            issues.append(StarterCoreLocationIssue(
                "starter_core_map_hash_invalid",
                f"/starter_core/map_definitions/{definition.map_id}",
                "Starter map hashes must exactly match their deterministic map content and semantic interface.",
                {
                    "definition_hash": definition.definition_hash,
                    "expected_definition_hash": rehashed.definition_hash,
                    "semantic_interface_hash": definition.semantic_interface_hash,
                    "expected_semantic_interface_hash": rehashed.semantic_interface_hash,
                },
            ))
        if not _has_arrival_spawn(definition):
            issues.append(StarterCoreLocationIssue(
                "starter_core_arrival_spawn_missing", f"/starter_core/map_definitions/{definition.map_id}/spawn_points",
                "Each starter core map requires an arrival spawn.", {},
            ))
    if settlement is not None and interior is not None and settlement.location_id in by_location and interior.location_id in by_location:
        settlement_definition = by_location[settlement.location_id]
        interior_definition = by_location[interior.location_id]
        if interior_definition.map_id not in _portal_targets(settlement_definition) or settlement_definition.map_id not in _portal_targets(interior_definition):
            issues.append(StarterCoreLocationIssue(
                "starter_core_portal_not_reciprocal", "/starter_core/map_definitions/portals",
                "Settlement and interior maps require reciprocal portals.",
                {"settlement_map_id": settlement_definition.map_id, "interior_map_id": interior_definition.map_id},
            ))
    materialization = {
        "contract_enabled": True,
        "starting_place_id": plan.starting_location_id,
        "settlement_slot": settlement.model_dump(mode="json") if settlement is not None else {},
        "interior_slot": interior.model_dump(mode="json") if interior is not None else {},
        "map_definitions": [definition.model_dump(mode="json") for definition in core_definitions],
    }
    unique = {(row.code, row.path): row for row in issues}
    return materialization, tuple(unique[key] for key in sorted(unique))


def starter_core_location_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    materialization, issues = _core_materialization(topic_rows, topic_graph)
    enabled = bool(materialization["contract_enabled"])
    return {
        "schema_version": "rpg_world_starter_core_location_report_v1",
        "passed": not issues,
        "issues": [row.as_dict() for row in issues],
        "materialization": materialization,
        "checks": {
            "contract_enabled": enabled,
            "settlement_materialized": not enabled or bool(materialization["settlement_slot"]),
            "interior_materialized": not enabled or bool(materialization["interior_slot"]),
            "core_map_count": len(materialization["map_definitions"]),
        },
    }


def require_valid_starter_core_locations(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> None:
    _materialization, issues = _core_materialization(topic_rows, topic_graph)
    if issues:
        raise StarterCoreLocationCompilationError(issues)


__all__ = [
    "StarterCoreLocationCompilationError",
    "StarterCoreLocationIssue",
    "require_valid_starter_core_locations",
    "starter_core_location_report",
]
