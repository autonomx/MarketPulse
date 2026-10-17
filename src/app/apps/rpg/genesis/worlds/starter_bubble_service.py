"""Transactional promotion of progressive starter maps into immutable world releases."""
from __future__ import annotations

from typing import Any, Mapping, cast

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from .contracts import MapDefinitionBinding, WorldReleaseDocument, WorldRevisionDocument
from .service import compile_world_release, compile_world_revision
from .starter_bubble import (
    build_starter_bubble,
    build_starter_map_definitions,
    predictive_materialization_queue,
    starter_bubble_certification,
)


def _latest_release_row(work: Any, context: Any, world_id: str, revision: int) -> Any:
    return work.connection.execute(
        "SELECT release, document_jsonb, release_hash, created_at "
        "FROM omnix_rpg_world_releases WHERE workspace_id = %s "
        "AND world_id = %s AND world_revision = %s "
        "ORDER BY release DESC LIMIT 1",
        (context.workspace_id, world_id, int(revision)),
    ).fetchone()


def _existing_promotion(
    work: Any,
    context: Any,
    *,
    world_id: str,
    source_world_revision: int,
) -> dict[str, Any] | None:
    row = work.connection.execute(
        "SELECT revision, document_jsonb, content_hash, created_at "
        "FROM omnix_rpg_world_revisions WHERE workspace_id = %s AND world_id = %s "
        "AND document_jsonb #>> '{provenance,starter_bubble,source_world_revision}' = %s "
        "ORDER BY revision DESC LIMIT 1",
        (context.workspace_id, world_id, str(source_world_revision)),
    ).fetchone()
    if row is None:
        return None
    release = _latest_release_row(work, context, world_id, int(row[0]))
    if release is None:
        return None
    return {
        "world_id": world_id,
        "world_revision": int(row[0]),
        "world_revision_hash": str(row[2]),
        "world_release": int(release[0]),
        "world_release_hash": str(release[2]),
        "world_document": dict(row[1]),
        "release_document": dict(release[1]),
        "created_at": row[3].isoformat(),
    }


def _merge_topology(
    source: Mapping[str, Any],
    starter_topology: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(source)
    existing_locations = [str(value) for value in source.get("locations") or ()]
    starter_locations = [str(value) for value in starter_topology.get("locations") or ()]
    existing_routes = [dict(row) for row in source.get("routes") or () if isinstance(row, Mapping)]
    starter_routes = [
        dict(row) for row in starter_topology.get("routes") or () if isinstance(row, Mapping)
    ]
    route_by_id = {
        str(row.get("route_id") or f"route:{index}"): row
        for index, row in enumerate([*existing_routes, *starter_routes], start=1)
    }
    merged.update(
        {
            "schema_version": "rpg_progressive_topology_v1",
            "locations": list(dict.fromkeys([*existing_locations, *starter_locations])),
            "routes": [route_by_id[key] for key in sorted(route_by_id)],
            "starter_bubble": dict(starter_topology),
        }
    )
    return merged


def promote_starter_bubble(
    *,
    world_id: str,
    source_world_revision: int,
    starting_location_id: str,
    neighboring_location_id: str | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    """Publish a future revision with navigable placeholders and exact map pins."""

    context = current_tenant()
    with unit_of_work(database) as work:
        existing = _existing_promotion(
            work,
            context,
            world_id=world_id,
            source_world_revision=source_world_revision,
        )
        if existing is not None:
            work.rollback()
            return {
                "ok": True,
                "status": "ready",
                "reused": True,
                "promotion": existing,
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
        current_row = work.connection.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM omnix_rpg_world_revisions "
            "WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        ).fetchone()
        current_revision = int(current_row[0])
        target_revision = current_revision + 1
        plan = build_starter_bubble(
            world_id=world_id,
            source_world_revision=source_world_revision,
            starting_location_id=starting_location_id,
            neighboring_location_id=neighboring_location_id,
        )
        definition_revisions: dict[str, int] = {}
        for slot in plan.map_slots():
            row = work.connection.execute(
                "SELECT COALESCE(MAX(definition_revision), 0) "
                "FROM omnix_rpg_map_definitions WHERE workspace_id = %s AND map_id = %s",
                (context.workspace_id, slot.map_id),
            ).fetchone()
            definition_revisions[str(slot.map_id)] = int(row[0]) + 1
        definitions = build_starter_map_definitions(
            plan,
            target_world_revision=target_revision,
            definition_revisions=definition_revisions,
        )
        bubble_certification = starter_bubble_certification(plan, definitions)
        blueprint_requirements = [
            dict(row) for row in source_revision.blueprint_requirements
        ]
        blueprint_requirements.extend(
            {
                "location_id": slot.location_id,
                "map_id": slot.map_id,
                "starter_role": slot.role,
                "deferred": slot.deferred,
                "simulation_readiness": slot.simulation_readiness,
                "presentation_readiness": slot.presentation_readiness,
            }
            for slot in plan.slots
        )
        promoted_revision = compile_world_revision(
            world_id=world_id,
            revision=target_revision,
            title=source_revision.title,
            canon=source_revision.canon,
            entity_manifest=source_revision.entity_manifest,
            topology=_merge_topology(source_revision.topology, plan.topology),
            adventure_seeds=source_revision.adventure_seeds,
            blueprint_requirements=blueprint_requirements,
            provenance={
                **dict(source_revision.provenance),
                "starter_bubble": {
                    "source_world_revision": source_world_revision,
                    "plan_schema_version": plan.schema_version,
                    "starting_location_id": starting_location_id,
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
        binding_by_map = {
            binding.map_id: binding for binding in source_release.map_bindings
        }
        stored_definitions: list[dict[str, Any]] = []
        for definition in definitions:
            stored = work.map_instances.put_definition(
                context,
                map_id=definition.map_id,
                definition_revision=definition.definition_revision,
                world_id=world_id,
                world_revision=target_revision,
                document=definition.model_dump(mode="json"),
                definition_hash=definition.definition_hash,
                semantic_interface_hash=definition.semantic_interface_hash,
            )
            stored_definitions.append(stored)
            binding_by_map[definition.map_id] = MapDefinitionBinding(
                map_id=definition.map_id,
                blueprint_revision=target_revision,
                definition_revision=definition.definition_revision,
                definition_hash=definition.definition_hash,
                semantic_interface_hash=definition.semantic_interface_hash,
                simulation_readiness="navigable",
                presentation_readiness=cast(Any, str(
                    definition.metadata.get("presentation_readiness")
                    or "placeholder"
                )),
            )
        bindings = [binding_by_map[key] for key in sorted(binding_by_map)]
        base_certification = dict(source_release.certification)
        launch_ready = bool(base_certification.get("launch_ready")) and bool(
            bubble_certification["simulation_certified"]
        )
        release_certification = {
            **base_certification,
            "launch_ready": launch_ready,
            "starter_bubble": bubble_certification,
            "simulation_readiness": "certified"
            if bubble_certification["simulation_certified"]
            else "failed",
            "presentation_readiness": "ready"
            if bubble_certification["presentation_complete"]
            else "assets_pending",
            "optional_art_blocks_gameplay": False,
        }
        promoted_release = compile_world_release(
            promoted_revision,
            release=1,
            map_bindings=bindings,
            indexes={
                **dict(source_release.indexes),
                "starter_bubble": plan.model_dump(mode="json"),
                "predictive_materialization": list(
                    predictive_materialization_queue(
                        plan,
                        current_location_id=starting_location_id,
                    )
                ),
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
                "source_world_revision": source_world_revision,
            },
            certification=release_certification,
        )
        stored_release = work.world_scenarios.publish_world_release(
            context,
            world_id=world_id,
            world_revision=int(stored_revision["revision"]),
            document=promoted_release.model_dump(mode="json"),
            release_hash=promoted_release.release_hash,
        )
        work.commit()

    promotion = {
        "world_id": world_id,
        "source_world_revision": source_world_revision,
        "world_revision": int(stored_revision["revision"]),
        "world_revision_hash": str(stored_revision["content_hash"]),
        "world_release": int(stored_release["release"]),
        "world_release_hash": str(stored_release["release_hash"]),
        "map_bindings": [binding.model_dump(mode="json") for binding in bindings],
        "map_definitions": stored_definitions,
        "starter_bubble": plan.model_dump(mode="json"),
        "certification": release_certification,
    }
    return {
        "ok": True,
        "status": "ready" if launch_ready else "review",
        "reused": False,
        "promotion": promotion,
    }
