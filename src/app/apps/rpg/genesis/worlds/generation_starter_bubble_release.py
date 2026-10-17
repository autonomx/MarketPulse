"""Integrated Release 6 certification for generated starter bubbles."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import canonical_content_hash
from .generation_starting_market import starting_market_report
from .generation_starter_bubble_support import derive_starter_bubble
from .generation_starter_core_locations import starter_core_location_report
from .generation_starter_neighbor import starter_neighbor_report
from .generation_starter_neighbor_artifacts import starter_neighbor_artifact_report
from .generation_starter_region import starter_region_report
from .generation_starter_topology import starter_topology_report
from .starter_bubble import (
    StarterBubblePlan,
    build_starter_map_definitions,
    starter_bubble_certification,
)


@dataclass(frozen=True)
class StarterBubbleReleaseIssue:
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


class StarterBubbleReleaseCompilationError(ValueError):
    def __init__(self, issues: Sequence[StarterBubbleReleaseIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(f"{row.code}:{row.path}" for row in self.issues)
        super().__init__("starter_bubble_release_integrity_failed:" + rendered)


def _component_reports(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    return {
        "starter_topology": starter_topology_report(topic_rows, topic_graph),
        "starter_region": starter_region_report(topic_rows, topic_graph),
        "starter_core_locations": starter_core_location_report(topic_rows, topic_graph),
        "starter_neighbor": starter_neighbor_report(topic_rows, topic_graph),
        "starter_neighbor_artifacts": starter_neighbor_artifact_report(
            topic_rows,
            topic_graph,
        ),
        "starting_market": starting_market_report(topic_rows, topic_graph),
    }


def _empty_certificate(*, enabled: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "rpg_world_starter_bubble_release_v1",
        "contract_enabled": enabled,
        "skipped": not enabled,
        "simulation_certified": not enabled,
        "presentation_complete": False,
        "optional_art_blocks_gameplay": False,
        "plan": {},
        "map_definitions": [],
        "native_certification": {},
        "component_statuses": {},
        "component_reports": {},
        "starting_market": {},
        "content_hash": "",
    }
    payload["content_hash"] = canonical_content_hash(payload)
    return payload


def _compile_certificate(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], tuple[StarterBubbleReleaseIssue, ...]]:
    derived = derive_starter_bubble(topic_rows, topic_graph)
    enabled = bool(derived["contract_enabled"])
    if not enabled:
        return _empty_certificate(enabled=False), ()

    issues: list[StarterBubbleReleaseIssue] = []

    def add(
        code: str,
        path: str,
        message: str,
        evidence: Mapping[str, Any],
    ) -> None:
        issues.append(StarterBubbleReleaseIssue(code, path, message, evidence))

    components = _component_reports(topic_rows, topic_graph)
    failed_components = sorted(
        name for name, report in components.items() if not bool(report.get("passed"))
    )
    if failed_components:
        add(
            "starter_bubble_release_component_failed",
            "/starter_bubble_release/components",
            "Every Release 6 starter-bubble component must pass independently.",
            {"failed_component_ids": failed_components},
        )

    plan = derived["plan"]
    definitions: tuple[Any, ...] = ()
    native: dict[str, Any] = {}
    if not isinstance(plan, StarterBubblePlan):
        add(
            "starter_bubble_release_plan_unavailable",
            "/starter_bubble_release/plan",
            "The integrated release requires a valid canonical starter-bubble plan.",
            {
                "starting_place_id": derived["starting_place_id"],
                "neighboring_place_id": derived["neighboring_place_id"],
                "starting_region_id": derived["starting_region_id"],
            },
        )
    else:
        definition_revision = max(1, int(plan.source_world_revision))
        definitions = build_starter_map_definitions(
            plan,
            target_world_revision=definition_revision,
            definition_revisions={
                slot.map_id: definition_revision
                for slot in plan.map_slots()
                if slot.map_id
            },
        )
        native = starter_bubble_certification(plan, definitions)
        if not bool(native.get("simulation_certified")):
            add(
                "starter_bubble_native_certification_failed",
                "/starter_bubble_release/native_certification",
                "The engine-native starter-bubble simulation certification must pass.",
                {
                    "missing_location_ids": list(native.get("missing_location_ids") or ()),
                    "failed_location_ids": list(native.get("failed_location_ids") or ()),
                },
            )
        required = {
            slot.location_id
            for slot in plan.slots
            if bool(slot.metadata.get("required_before_launch"))
        }
        native_required = {
            str(value) for value in native.get("required_location_ids") or () if str(value)
        }
        materialized = {
            str(value)
            for value in native.get("materialized_location_ids") or ()
            if str(value)
        }
        if required != native_required:
            add(
                "starter_bubble_required_manifest_mismatch",
                "/starter_bubble_release/native_certification/required_location_ids",
                "Native required locations must exactly match launch-required starter slots.",
                {
                    "slot_required_location_ids": sorted(required),
                    "native_required_location_ids": sorted(native_required),
                },
            )
        missing_required = sorted(required - materialized)
        if missing_required:
            add(
                "starter_bubble_required_map_missing",
                "/starter_bubble_release/map_definitions",
                "Every launch-required starter location must have an immediate map definition.",
                {"missing_location_ids": missing_required},
            )
        deferred = {slot.location_id for slot in plan.slots if slot.deferred}
        native_deferred = {
            str(value) for value in native.get("deferred_location_ids") or () if str(value)
        }
        if not deferred or deferred != native_deferred:
            add(
                "starter_bubble_deferred_manifest_mismatch",
                "/starter_bubble_release/native_certification/deferred_location_ids",
                "Native deferred locations must exactly match declared frontier blueprints.",
                {
                    "slot_deferred_location_ids": sorted(deferred),
                    "native_deferred_location_ids": sorted(native_deferred),
                },
            )
        if bool(native.get("optional_art_blocks_gameplay")):
            add(
                "starter_bubble_optional_art_blocks_gameplay",
                "/starter_bubble_release/native_certification/optional_art_blocks_gameplay",
                "Optional presentation assets must never block starter-bubble gameplay.",
                {},
            )

    component_statuses = {
        name: bool(report.get("passed")) for name, report in components.items()
    }
    certificate: dict[str, Any] = {
        "schema_version": "rpg_world_starter_bubble_release_v1",
        "contract_enabled": True,
        "skipped": False,
        "simulation_certified": not issues,
        "presentation_complete": bool(native.get("presentation_complete")),
        "optional_art_blocks_gameplay": bool(
            native.get("optional_art_blocks_gameplay", False)
        ),
        "plan": plan.model_dump(mode="json") if isinstance(plan, StarterBubblePlan) else {},
        "map_definitions": [
            definition.model_dump(mode="json") for definition in definitions
        ],
        "native_certification": native,
        "component_statuses": component_statuses,
        "component_reports": components,
        "starting_market": dict(
            components.get("starting_market", {}).get("materialization") or {}
        ),
        "content_hash": "",
    }
    certificate["content_hash"] = canonical_content_hash(certificate)
    unique = {(row.code, row.path): row for row in issues}
    return certificate, tuple(unique[key] for key in sorted(unique))


def starter_bubble_release_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    certificate, issues = _compile_certificate(topic_rows, topic_graph)
    enabled = bool(certificate["contract_enabled"])
    return {
        "schema_version": "rpg_world_starter_bubble_release_report_v1",
        "passed": not issues,
        "issues": [row.as_dict() for row in issues],
        "materialization": certificate,
        "checks": {
            "contract_enabled": enabled,
            "skipped_when_not_declared": enabled or bool(certificate["skipped"]),
            "all_components_passed": not enabled
            or all(certificate["component_statuses"].values()),
            "native_simulation_certified": not enabled
            or bool(certificate["native_certification"].get("simulation_certified")),
            "certificate_hashed": str(certificate["content_hash"]).startswith("sha256:"),
            "optional_art_non_blocking": not bool(
                certificate["optional_art_blocks_gameplay"]
            ),
        },
    }


def require_valid_starter_bubble_release(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> None:
    _certificate, issues = _compile_certificate(topic_rows, topic_graph)
    if issues:
        raise StarterBubbleReleaseCompilationError(issues)


__all__ = [
    "StarterBubbleReleaseCompilationError",
    "StarterBubbleReleaseIssue",
    "require_valid_starter_bubble_release",
    "starter_bubble_release_report",
]
