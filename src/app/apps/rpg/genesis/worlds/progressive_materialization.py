"""Safe progressive materialization of deferred maps into explicit future releases."""
from __future__ import annotations

from typing import Any, Mapping, cast

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from .contracts import MapDefinitionBinding, WorldReleaseDocument, WorldRevisionDocument
from .service import compile_world_release, compile_world_revision
from .starter_bubble import (
    StarterBubblePlan,
    StarterLocationSlot,
    build_starter_map_definitions,
    predictive_materialization_queue,
)


def _latest_release_row(work: Any, context: Any, world_id: str, revision: int) -> Any:
    return work.connection.execute(
        "SELECT release, document_jsonb, release_hash, created_at "
        "FROM omnix_rpg_world_releases WHERE workspace_id = %s "
        "AND world_id = %s AND world_revision = %s "
        "ORDER BY release DESC LIMIT 1",
        (context.workspace_id, world_id, int(revision)),
    ).fetchone()


def _existing_materialization(
    work: Any,
    context: Any,
    *,
    world_id: str,
    source_world_revision: int,
    location_id: str,
) -> dict[str, Any] | None:
    row = work.connection.execute(
        "SELECT revision, document_jsonb, content_hash, created_at "
        "FROM omnix_rpg_world_revisions WHERE workspace_id = %s AND world_id = %s "
        "AND document_jsonb #>> "
        "'{provenance,progressive_materialization,source_world_revision}' = %s "
        "AND document_jsonb #>> "
        "'{provenance,progressive_materialization,location_id}' = %s "
        "ORDER BY revision DESC LIMIT 1",
        (
            context.workspace_id,
            world_id,
            str(source_world_revision),
            location_id,
        ),
    ).fetchone()
    if row is None:
        return None
    release = _latest_release_row(work, context, world_id, int(row[0]))
    if release is None:
        return None
    release_document = dict(release[1])
    certification = release_document.get("certification")
    certification = dict(certification) if isinstance(certification, Mapping) else {}
    return {
        "status": "ready" if certification.get("launch_ready") else "review",
        "world_id": world_id,
        "source_world_revision": source_world_revision,
        "location_id": location_id,
        "world_revision": int(row[0]),
        "world_revision_hash": str(row[2]),
        "world_release": int(release[0]),
        "world_release_hash": str(release[2]),
        "world_document": dict(row[1]),
        "release_document": release_document,
        "created_at": row[3].isoformat(),
    }


def _starter_plan(
    source_revision: WorldRevisionDocument,
    source_release: WorldReleaseDocument,
) -> StarterBubblePlan:
    payload = source_release.indexes.get("starter_bubble")
    if not isinstance(payload, Mapping):
        payload = source_revision.topology.get("starter_bubble")
    if not isinstance(payload, Mapping):
        raise ValueError("starter_bubble_plan_missing")
    return StarterBubblePlan.model_validate(payload)


def _updated_plan(
    plan: StarterBubblePlan,
    location_id: str,
) -> StarterBubblePlan:
    slot = plan.slot(location_id)
    if not slot.deferred:
        raise ValueError(f"location_not_deferred:{location_id}")
    if not slot.map_id:
        raise ValueError(f"deferred_location_map_missing:{location_id}")
    slots = tuple(
        current.model_copy(
            update={
                "deferred": False,
                "simulation_readiness": "navigable",
            }
        )
        if current.location_id == location_id
        else current
        for current in plan.slots
    )
    topology = dict(plan.topology)
    topology["deferred_location_ids"] = [
        current.location_id for current in slots if current.deferred
    ]
    topology["materialized_location_ids"] = sorted(
        current.location_id
        for current in slots
        if current.map_id and not current.deferred
    )
    return plan.model_copy(update={"slots": slots, "topology": topology})


def _affected_slots(
    plan: StarterBubblePlan,
    location_id: str,
) -> tuple[StarterLocationSlot, ...]:
    """Return the target and every materialized neighbor whose portals change."""

    target = plan.slot(location_id)
    affected = []
    for slot in plan.slots:
        if not slot.map_id or slot.deferred:
            continue
        if (
            slot.location_id == location_id
            or slot.location_id in target.connected_location_ids
            or location_id in slot.connected_location_ids
        ):
            affected.append(slot)
    return tuple(sorted(affected, key=lambda value: value.location_id))


def _definition_revisions(
    work: Any,
    context: Any,
    slots: tuple[StarterLocationSlot, ...],
) -> dict[str, int]:
    revisions: dict[str, int] = {}
    for slot in slots:
        if not slot.map_id:
            continue
        row = work.connection.execute(
            "SELECT COALESCE(MAX(definition_revision), 0) "
            "FROM omnix_rpg_map_definitions WHERE workspace_id = %s AND map_id = %s",
            (context.workspace_id, slot.map_id),
        ).fetchone()
        revisions[slot.map_id] = int(row[0]) + 1
    return revisions


def _promoted_blueprint_requirements(
    source_revision: WorldRevisionDocument,
    *,
    location_id: str,
    map_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    replaced = False
    for value in source_revision.blueprint_requirements:
        row = dict(value)
        if str(row.get("location_id") or "") == location_id:
            row.update(
                {
                    "map_id": map_id,
                    "deferred": False,
                    "simulation_readiness": "navigable",
                    "presentation_readiness": str(
                        row.get("presentation_readiness") or "assets_pending"
                    ),
                }
            )
            replaced = True
        rows.append(row)
    if not replaced:
        rows.append(
            {
                "location_id": location_id,
                "map_id": map_id,
                "deferred": False,
                "simulation_readiness": "navigable",
                "presentation_readiness": "assets_pending",
            }
        )
    return rows


def materialize_deferred_location(
    *,
    world_id: str,
    source_world_revision: int,
    location_id: str,
    database: Any | None = None,
) -> dict[str, Any]:
    """Materialize one deferred slot without mutating or rebinding existing campaigns."""

    context = current_tenant()
    with unit_of_work(database) as work:
        existing = _existing_materialization(
            work,
            context,
            world_id=world_id,
            source_world_revision=source_world_revision,
            location_id=location_id,
        )
        if existing is not None:
            work.rollback()
            return {
                "ok": True,
                "status": existing["status"],
                "reused": True,
                "materialization": existing,
            }

        world = work.world_scenarios.get_world(context, world_id, for_update=True)
        if world is None:
            raise KeyError(f"world_not_found:{world_id}")
        source_row = work.world_scenarios.get_world_revision(
            context,
            world_id,
            source_world_revision,
        )
        if source_row is None:
            raise KeyError(
                f"world_revision_not_found:{world_id}:{source_world_revision}"
            )
        source_release_row = _latest_release_row(
            work,
            context,
            world_id,
            source_world_revision,
        )
        if source_release_row is None:
            raise KeyError(
                f"world_release_not_found:{world_id}:{source_world_revision}"
            )
        source_revision = WorldRevisionDocument.model_validate(source_row["document"])
        source_release = WorldReleaseDocument.model_validate(source_release_row[1])
        plan = _starter_plan(source_revision, source_release)
        promoted_plan = _updated_plan(plan, location_id)
        target_slot = promoted_plan.slot(location_id)
        if not target_slot.map_id:
            raise ValueError(f"deferred_location_map_missing:{location_id}")

        current_row = work.connection.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM omnix_rpg_world_revisions "
            "WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        ).fetchone()
        current_revision = int(current_row[0])
        target_revision = current_revision + 1
        affected_slots = _affected_slots(promoted_plan, location_id)
        affected_map_ids = {
            slot.map_id for slot in affected_slots if slot.map_id is not None
        }
        all_definitions = build_starter_map_definitions(
            promoted_plan,
            target_world_revision=target_revision,
            definition_revisions=_definition_revisions(
                work,
                context,
                affected_slots,
            ),
        )
        definitions = tuple(
            definition
            for definition in all_definitions
            if definition.map_id in affected_map_ids
        )
        target_definition = next(
            value for value in definitions if value.map_id == target_slot.map_id
        )

        topology = dict(source_revision.topology)
        topology["starter_bubble"] = dict(promoted_plan.topology)
        promoted_revision = compile_world_revision(
            world_id=world_id,
            revision=target_revision,
            title=source_revision.title,
            canon=source_revision.canon,
            entity_manifest=source_revision.entity_manifest,
            topology=topology,
            adventure_seeds=source_revision.adventure_seeds,
            blueprint_requirements=_promoted_blueprint_requirements(
                source_revision,
                location_id=location_id,
                map_id=target_slot.map_id,
            ),
            provenance={
                **dict(source_revision.provenance),
                "progressive_materialization": {
                    "source_world_revision": source_world_revision,
                    "location_id": location_id,
                    "map_id": target_slot.map_id,
                    "affected_map_ids": sorted(affected_map_ids),
                    "promotion_mode": "explicit_future_revision",
                },
            },
        )
        stored_revision = work.world_scenarios.publish_world_revision(
            context,
            world_id=world_id,
            document=promoted_revision.model_dump(mode="json"),
            content_hash=promoted_revision.content_hash,
            expected_revision=current_revision,
        )
        stored_definitions: list[dict[str, Any]] = []
        binding_by_map = {
            binding.map_id: binding for binding in source_release.map_bindings
        }
        for definition in definitions:
            stored_definitions.append(
                work.map_instances.put_definition(
                    context,
                    map_id=definition.map_id,
                    definition_revision=definition.definition_revision,
                    world_id=world_id,
                    world_revision=target_revision,
                    document=definition.model_dump(mode="json"),
                    definition_hash=definition.definition_hash,
                    semantic_interface_hash=definition.semantic_interface_hash,
                )
            )
            binding_by_map[definition.map_id] = MapDefinitionBinding(
                map_id=definition.map_id,
                blueprint_revision=target_revision,
                definition_revision=definition.definition_revision,
                definition_hash=definition.definition_hash,
                semantic_interface_hash=definition.semantic_interface_hash,
                simulation_readiness="navigable",
                presentation_readiness=cast(Any, str(
                    definition.metadata.get("presentation_readiness")
                    or "assets_pending"
                )),
            )
        bindings = [binding_by_map[key] for key in sorted(binding_by_map)]
        base_certification = dict(source_release.certification)
        launch_ready = bool(base_certification.get("launch_ready"))
        certification = {
            **base_certification,
            "launch_ready": launch_ready,
            "progressive_materialization": {
                "location_id": location_id,
                "map_id": target_definition.map_id,
                "affected_map_ids": sorted(affected_map_ids),
                "simulation_readiness": "navigable",
                "presentation_readiness": str(
                    target_definition.metadata.get("presentation_readiness")
                    or "assets_pending"
                ),
                "optional_art_blocks_gameplay": False,
            },
            "optional_art_blocks_gameplay": False,
        }
        asset_bindings = dict(source_release.asset_bindings)
        for definition in definitions:
            asset_bindings[definition.map_id] = {
                "status": "optional",
                "fallback": "semantic_grid_placeholder",
            }
        promoted_release = compile_world_release(
            promoted_revision,
            release=1,
            map_bindings=bindings,
            indexes={
                **dict(source_release.indexes),
                "starter_bubble": promoted_plan.model_dump(mode="json"),
                "predictive_materialization": list(
                    predictive_materialization_queue(
                        promoted_plan,
                        current_location_id=location_id,
                    )
                ),
            },
            asset_bindings=asset_bindings,
            compiler_provenance={
                **dict(source_release.compiler_provenance),
                "progressive_map_compiler": "rpg_progressive_map_v1",
                "source_world_revision": source_world_revision,
                "location_id": location_id,
                "affected_map_ids": sorted(affected_map_ids),
            },
            certification=certification,
        )
        stored_release = work.world_scenarios.publish_world_release(
            context,
            world_id=world_id,
            world_revision=int(stored_revision["revision"]),
            document=promoted_release.model_dump(mode="json"),
            release_hash=promoted_release.release_hash,
        )
        work.commit()

    materialization = {
        "world_id": world_id,
        "source_world_revision": source_world_revision,
        "location_id": location_id,
        "world_revision": int(stored_revision["revision"]),
        "world_revision_hash": str(stored_revision["content_hash"]),
        "world_release": int(stored_release["release"]),
        "world_release_hash": str(stored_release["release_hash"]),
        "map_binding": binding_by_map[target_definition.map_id].model_dump(mode="json"),
        "map_bindings": [
            binding_by_map[map_id].model_dump(mode="json")
            for map_id in sorted(affected_map_ids)
        ],
        "map_definition": next(
            row
            for row in stored_definitions
            if row["map_id"] == target_definition.map_id
        ),
        "map_definitions": stored_definitions,
        "starter_bubble": promoted_plan.model_dump(mode="json"),
        "certification": certification,
    }
    return {
        "ok": True,
        "status": "ready" if launch_ready else "review",
        "reused": False,
        "materialization": materialization,
    }
