"""Publish a certified starter bubble into the initial immutable world release."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, cast

from app.apps.rpg.world.map_grid_contracts import GridMapDefinition

from .contracts import MapDefinitionBinding, canonical_content_hash
from .generation_publication import WorldGenerationPublication
from .service import compile_world_release, compile_world_revision
from .starter_bubble import (
    StarterBubblePlan,
    predictive_materialization_queue,
    starter_bubble_certification,
)


class StarterBubblePublicationError(ValueError):
    def __init__(self, issues: Sequence[Mapping[str, Any]]) -> None:
        self.issues = tuple(dict(issue) for issue in issues)
        codes = ",".join(str(issue.get("code") or "unknown") for issue in self.issues)
        super().__init__("starter_bubble_publication_failed:" + codes)


@dataclass(frozen=True)
class CertifiedStarterBubblePublication:
    publication: WorldGenerationPublication
    map_definitions: tuple[GridMapDefinition, ...]
    report: Mapping[str, Any]


def _merge_topology(
    source: Mapping[str, Any],
    starter: Mapping[str, Any],
) -> dict[str, Any]:
    existing_locations = [str(value) for value in source.get("locations") or ()]
    starter_locations = [str(value) for value in starter.get("locations") or ()]
    existing_routes = [
        dict(row) for row in source.get("routes") or () if isinstance(row, Mapping)
    ]
    starter_routes = [
        dict(row) for row in starter.get("routes") or () if isinstance(row, Mapping)
    ]
    route_by_id = {
        str(row.get("route_id") or f"route:{index}"): row
        for index, row in enumerate([*existing_routes, *starter_routes], start=1)
    }
    return {
        **dict(source),
        "schema_version": "rpg_progressive_topology_v1",
        "locations": list(dict.fromkeys([*existing_locations, *starter_locations])),
        "routes": [route_by_id[key] for key in sorted(route_by_id)],
        "starter_bubble": dict(starter),
    }


def _blueprint_requirements(
    source: Sequence[Mapping[str, Any]],
    plan: StarterBubblePlan,
) -> tuple[dict[str, Any], ...]:
    by_map = {
        str(row.get("map_id") or ""): dict(row)
        for row in source
        if str(row.get("map_id") or "")
    }
    for slot in plan.slots:
        if not slot.map_id:
            continue
        by_map[slot.map_id] = {
            "location_id": slot.location_id,
            "map_id": slot.map_id,
            "starter_role": slot.role,
            "deferred": slot.deferred,
            "simulation_readiness": slot.simulation_readiness,
            "presentation_readiness": slot.presentation_readiness,
        }
    return tuple(by_map[key] for key in sorted(by_map))


def _map_bindings(
    publication: WorldGenerationPublication,
    definitions: Sequence[GridMapDefinition],
) -> tuple[MapDefinitionBinding, ...]:
    by_map = {
        binding.map_id: binding
        for binding in publication.world_release.map_bindings
    }
    for definition in definitions:
        by_map[definition.map_id] = MapDefinitionBinding(
            map_id=definition.map_id,
            blueprint_revision=publication.world_revision.revision,
            definition_revision=definition.definition_revision,
            definition_hash=definition.definition_hash,
            semantic_interface_hash=definition.semantic_interface_hash,
            simulation_readiness="navigable",
            presentation_readiness=cast(Any, str(
                definition.metadata.get("presentation_readiness") or "placeholder"
            )),
        )
    return tuple(by_map[key] for key in sorted(by_map))


def _empty_report(*, enabled: bool) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "rpg_world_starter_bubble_publication_v1",
        "passed": True,
        "contract_enabled": enabled,
        "skipped": not enabled,
        "map_definition_count": 0,
        "map_binding_count": 0,
        "starting_location_id": "",
        "deferred_location_ids": [],
        "certificate_hash": "",
        "content_hash": "",
    }
    report["content_hash"] = canonical_content_hash(report)
    return report


def _issue(code: str, **evidence: Any) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "error",
        "blocking": True,
        "evidence": evidence,
    }


def _validated_inputs(
    publication: WorldGenerationPublication,
    certificate: Mapping[str, Any],
) -> tuple[StarterBubblePlan, tuple[GridMapDefinition, ...], dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    supplied_hash = str(certificate.get("content_hash") or "")
    expected_hash = canonical_content_hash({**dict(certificate), "content_hash": ""})
    if supplied_hash != expected_hash:
        issues.append(
            _issue(
                "starter_bubble_certificate_hash_mismatch",
                supplied_hash=supplied_hash,
                expected_hash=expected_hash,
            )
        )
    try:
        plan = StarterBubblePlan.model_validate(certificate.get("plan") or {})
    except (TypeError, ValueError) as exc:
        issues.append(_issue("starter_bubble_plan_invalid", error=str(exc)))
        plan = None
    definitions: tuple[GridMapDefinition, ...] = ()
    try:
        definitions = tuple(
            GridMapDefinition.model_validate(row)
            for row in certificate.get("map_definitions") or ()
        )
    except (TypeError, ValueError) as exc:
        issues.append(_issue("starter_bubble_map_definition_invalid", error=str(exc)))
    if not bool(certificate.get("simulation_certified")):
        issues.append(_issue("starter_bubble_certificate_not_simulation_ready"))
    if bool(certificate.get("optional_art_blocks_gameplay")):
        issues.append(_issue("starter_bubble_optional_art_blocks_publication"))
    revision = publication.world_revision
    if plan is not None:
        if plan.world_id != revision.world_id:
            issues.append(
                _issue(
                    "starter_bubble_world_mismatch",
                    plan_world_id=plan.world_id,
                    publication_world_id=revision.world_id,
                )
            )
        if plan.source_world_revision != revision.revision:
            issues.append(
                _issue(
                    "starter_bubble_revision_mismatch",
                    plan_revision=plan.source_world_revision,
                    publication_revision=revision.revision,
                )
            )
        deferred_map_ids = {
            slot.map_id for slot in plan.slots if slot.deferred and slot.map_id
        }
        materialized_map_ids = {definition.map_id for definition in definitions}
        eager_deferred = sorted(deferred_map_ids.intersection(materialized_map_ids))
        if eager_deferred:
            issues.append(
                _issue(
                    "starter_bubble_deferred_map_materialized",
                    map_ids=eager_deferred,
                )
            )
    for definition in definitions:
        if definition.world_id != revision.world_id:
            issues.append(
                _issue(
                    "starter_map_world_mismatch",
                    map_id=definition.map_id,
                    definition_world_id=definition.world_id,
                    publication_world_id=revision.world_id,
                )
            )
        if definition.world_revision != revision.revision:
            issues.append(
                _issue(
                    "starter_map_revision_mismatch",
                    map_id=definition.map_id,
                    definition_revision=definition.world_revision,
                    publication_revision=revision.revision,
                )
            )
    if plan is not None and definitions:
        native = starter_bubble_certification(plan, definitions)
        if not bool(native.get("simulation_certified")):
            issues.append(
                _issue(
                    "starter_bubble_recertification_failed",
                    missing_location_ids=list(native.get("missing_location_ids") or ()),
                    failed_location_ids=list(native.get("failed_location_ids") or ()),
                )
            )
    else:
        native = {}
    if issues:
        raise StarterBubblePublicationError(issues)
    assert plan is not None
    return plan, definitions, native


def apply_certified_starter_bubble(
    publication: WorldGenerationPublication,
    certificate: Mapping[str, Any] | None,
) -> CertifiedStarterBubblePublication:
    payload = dict(certificate or {})
    enabled = bool(payload.get("contract_enabled"))
    if not enabled:
        return CertifiedStarterBubblePublication(
            publication=publication,
            map_definitions=(),
            report=_empty_report(enabled=False),
        )
    plan, definitions, native = _validated_inputs(publication, payload)
    source_revision = publication.world_revision
    source_release = publication.world_release
    revision = compile_world_revision(
        world_id=source_revision.world_id,
        revision=source_revision.revision,
        title=source_revision.title,
        canon=source_revision.canon,
        entity_manifest=source_revision.entity_manifest,
        topology=_merge_topology(source_revision.topology, plan.topology),
        adventure_seeds=source_revision.adventure_seeds,
        blueprint_requirements=_blueprint_requirements(
            source_revision.blueprint_requirements,
            plan,
        ),
        provenance={
            **dict(source_revision.provenance),
            "starter_bubble_publication": {
                "schema_version": plan.schema_version,
                "starting_location_id": plan.starting_location_id,
                "certificate_hash": str(payload.get("content_hash") or ""),
                "publication_mode": "atomic_initial_release",
            },
        },
    )
    bindings = _map_bindings(publication, definitions)
    queue = list(
        predictive_materialization_queue(
            plan,
            current_location_id=plan.starting_location_id,
        )
    )
    report: dict[str, Any] = {
        "schema_version": "rpg_world_starter_bubble_publication_v1",
        "passed": True,
        "contract_enabled": True,
        "skipped": False,
        "map_definition_count": len(definitions),
        "map_binding_count": len(bindings),
        "starting_location_id": plan.starting_location_id,
        "deferred_location_ids": list(native.get("deferred_location_ids") or ()),
        "certificate_hash": str(payload.get("content_hash") or ""),
        "content_hash": "",
    }
    report["content_hash"] = canonical_content_hash(report)
    certification = {
        **dict(publication.certification),
        "starter_bubble": native,
        "starter_bubble_publication": report,
        "simulation_readiness": "certified",
        "presentation_readiness": (
            "ready" if bool(native.get("presentation_complete")) else "assets_pending"
        ),
        "optional_art_blocks_gameplay": False,
    }
    release = compile_world_release(
        revision,
        release=source_release.release,
        map_bindings=bindings,
        indexes={
            **dict(source_release.indexes),
            "starter_bubble": plan.model_dump(mode="json"),
            "predictive_materialization": queue,
            "starting_market": dict(payload.get("starting_market") or {}),
        },
        asset_bindings={
            **dict(source_release.asset_bindings),
            "starter_bubble": {
                "status": "optional",
                "fallback": "semantic_grid_placeholder",
            },
        },
        compiler_provenance={
            **dict(source_release.compiler_provenance),
            "starter_bubble_compiler": "rpg_starter_bubble_v1",
            "starter_bubble_certificate_hash": str(payload.get("content_hash") or ""),
        },
        certification=certification,
        artifact_stage=source_release.artifact_stage,
        runtime_seed=source_release.runtime_seed,
        materialization=source_release.materialization,
        playtest_report=source_release.playtest_report,
    )
    return CertifiedStarterBubblePublication(
        publication=WorldGenerationPublication(
            world_revision=revision,
            world_release=release,
            certification=certification,
        ),
        map_definitions=definitions,
        report=report,
    )


def persist_certified_starter_maps(
    work: Any,
    context: Any,
    bundle: CertifiedStarterBubblePublication,
) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = []
    for definition in bundle.map_definitions:
        stored.append(
            work.map_instances.put_definition(
                context,
                map_id=definition.map_id,
                definition_revision=definition.definition_revision,
                world_id=definition.world_id,
                world_revision=definition.world_revision,
                document=definition.model_dump(mode="json"),
                definition_hash=definition.definition_hash,
                semantic_interface_hash=definition.semantic_interface_hash,
            )
        )
    return stored


__all__ = [
    "CertifiedStarterBubblePublication",
    "StarterBubblePublicationError",
    "apply_certified_starter_bubble",
    "persist_certified_starter_maps",
]
