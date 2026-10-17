"""Atomic certified publication for durable World Forge runs."""
from __future__ import annotations

from typing import Any, Mapping, Sequence, cast

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from .generation_compilation import compile_world_generation_certified_artifact
from .generation_exact_artifact import (
    rebind_exact_artifact_report,
    require_exact_artifact_binding,
)
from .generation_profile_release_contracts import require_profile_release_contracts
from .generation_publication import WorldGenerationPublication
from .generation_publication_transaction import (
    require_certified_publication,
    require_publication_run_ready,
)
from .generation_starter_bubble_publication import (
    StarterBubblePublicationError,
    apply_certified_starter_bubble,
    persist_certified_starter_maps,
)
from .lifecycle_service import require_world_writable
from .map_blueprint_authoring import (
    latest_ready_blueprint_requirements,
    materialize_generated_location_blueprints,
)
from .map_blueprint_publication import merge_authored_blueprints
from .service import compile_world_release
from .world_image_bindings import approved_world_asset_bindings


def _release_with_certification(
    publication: WorldGenerationPublication,
    *,
    certification: Mapping[str, Any],
) -> WorldGenerationPublication:
    release = publication.world_release
    rebuilt = compile_world_release(
        publication.world_revision,
        release=release.release,
        map_bindings=release.map_bindings,
        indexes=release.indexes,
        asset_bindings=release.asset_bindings,
        compiler_provenance=release.compiler_provenance,
        certification=certification,
        artifact_stage=release.artifact_stage,
        runtime_seed=release.runtime_seed,
        materialization=release.materialization,
        playtest_report=release.playtest_report,
    )
    return WorldGenerationPublication(
        world_revision=publication.world_revision,
        world_release=rebuilt,
        certification=dict(certification),
    )


def _compilation_run(
    run: Mapping[str, Any],
    review_results: Sequence[Mapping[str, Any]],
    *,
    world_id: str,
    target_revision: int,
) -> dict[str, Any]:
    graph = dict(run.get("graph") or {})
    metadata = dict(graph.get("metadata") or {})
    graph["metadata"] = {
        **metadata,
        "world_id": world_id,
        "world_revision": int(target_revision),
    }
    graph = require_profile_release_contracts(graph)
    return {
        **dict(run),
        "graph": graph,
        "_review_results": list(review_results),
    }


def _starter_certificate(certification: Mapping[str, Any]) -> Mapping[str, Any] | None:
    report = certification.get("starter_bubble_release")
    if not isinstance(report, Mapping):
        return None
    materialization = report.get("materialization")
    return dict(materialization) if isinstance(materialization, Mapping) else None


def _required_starter_certificate(
    compilation_run: Mapping[str, Any],
    certification: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    graph = dict(compilation_run.get("graph") or {})
    metadata = dict(graph.get("metadata") or {})
    contract = metadata.get("starter_bubble_contract")
    required = isinstance(contract, Mapping) and bool(contract.get("required"))
    certificate = _starter_certificate(certification)
    if required and (
        certificate is None or not bool(certificate.get("contract_enabled"))
    ):
        raise StarterBubblePublicationError(
            (
                {
                    "code": "starter_bubble_certificate_required",
                    "severity": "error",
                    "blocking": True,
                    "evidence": {
                        "contract": dict(cast(Any, contract)),
                        "certificate_present": certificate is not None,
                    },
                },
            )
        )
    return certificate


def _final_certification(
    publication: WorldGenerationPublication,
    *,
    authored_map_blueprint_count: int,
    starter_map_definition_count: int,
) -> dict[str, Any]:
    certification = dict(publication.certification)
    prior_binding = certification.get("exact_artifact_binding")
    if isinstance(prior_binding, Mapping):
        exact_binding = rebind_exact_artifact_report(
            prior_binding,
            publication.world_revision,
        )
        certification["exact_artifact_binding"] = exact_binding
        require_exact_artifact_binding(exact_binding)
    certification["authored_map_blueprint_count"] = int(
        authored_map_blueprint_count
    )
    certification["starter_map_definition_count"] = int(
        starter_map_definition_count
    )
    certification["starter_map_binding_count"] = len(
        publication.world_release.map_bindings
    )
    return certification


def publish_certified_world_generation(
    run_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    """Persist a revision, starter maps and release in one certified transaction."""

    context = current_tenant()
    with unit_of_work(database) as work:
        run = work.world_generation.get(context, run_id)
        if run is None:
            raise KeyError(f"world_generation_run_not_found:{run_id}")
        publication = dict(run.get("plan") or {}).get("publication")
        if isinstance(publication, Mapping):
            work.rollback()
            return {
                "ok": True,
                "status": "ready",
                "run": run,
                "publication": dict(publication),
                "reused": True,
            }

        require_publication_run_ready(run)
        world_id = str(run.get("world_id") or "")
        world = require_world_writable(work, context, world_id)
        topic_rows = work.world_generation.list_topics(
            context,
            world_id=world_id,
            draft_revision=int(run.get("draft_revision") or 1),
        )
        review_results = work.world_generation.list_topic_results(
            context,
            run_id=run_id,
        )
        current_row = work.connection.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM omnix_rpg_world_revisions "
            "WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        ).fetchone()
        current_revision = int(current_row[0])
        target_revision = current_revision + 1
        compilation_run = _compilation_run(
            run,
            review_results,
            world_id=world_id,
            target_revision=target_revision,
        )
        asset_bindings = approved_world_asset_bindings(work, context, world_id)

        artifact = compile_world_generation_certified_artifact(
            run=compilation_run,
            world=world,
            topic_rows=topic_rows,
            revision=target_revision,
            asset_bindings=asset_bindings,
        )
        compiled = artifact.publication
        require_certified_publication(run, artifact.certification)

        canon_entities = compiled.world_revision.canon.get("entities")
        materialization = dict(compiled.world_release.materialization)
        selected_locations = {
            str(materialization.get("hub_location_id") or ""),
            *(str(item) for item in materialization.get("sublocation_ids") or ()),
            *(str(item) for item in materialization.get("nearby_location_ids") or ()),
        }
        selected_locations.discard("")
        generated_locations = {
            str(entity_id): dict(entity)
            for entity_id, entity in dict(canon_entities or {}).items()
            if isinstance(entity, Mapping) and str(entity_id) in selected_locations
        }
        materialize_generated_location_blueprints(
            work,
            context,
            world_id,
            generated_locations,
        )
        requirements = latest_ready_blueprint_requirements(work, context, world_id)
        revision_document, release_document = merge_authored_blueprints(
            compiled.world_revision,
            compiled.world_release,
            requirements,
        )
        assembled_certification = {
            **dict(artifact.certification),
            "authored_map_blueprint_count": len(requirements),
        }
        compiled = _release_with_certification(
            WorldGenerationPublication(
                world_revision=revision_document,
                world_release=release_document,
                certification=assembled_certification,
            ),
            certification=assembled_certification,
        )
        starter_bundle = apply_certified_starter_bubble(
            compiled,
            _required_starter_certificate(
                compilation_run,
                artifact.certification,
            ),
        )
        compiled = starter_bundle.publication
        final_certification = _final_certification(
            compiled,
            authored_map_blueprint_count=len(requirements),
            starter_map_definition_count=len(starter_bundle.map_definitions),
        )
        compiled = _release_with_certification(
            compiled,
            certification=final_certification,
        )
        require_certified_publication(run, compiled.certification)

        stored_revision = work.world_scenarios.publish_world_revision(
            context,
            world_id=world_id,
            document=compiled.world_revision.model_dump(mode="json"),
            content_hash=compiled.world_revision.content_hash,
            expected_revision=current_revision,
        )
        stored_starter_maps = persist_certified_starter_maps(
            work,
            context,
            starter_bundle,
        )
        stored_release = work.world_scenarios.publish_world_release(
            context,
            world_id=world_id,
            world_revision=int(stored_revision["revision"]),
            document=compiled.world_release.model_dump(mode="json"),
            release_hash=compiled.world_release.release_hash,
        )
        publication_payload = {
            "world_id": world_id,
            "world_revision": int(stored_revision["revision"]),
            "world_revision_hash": str(stored_revision["content_hash"]),
            "world_release": int(stored_release["release"]),
            "world_release_hash": str(stored_release["release_hash"]),
            "artifact_stage": compiled.world_release.artifact_stage,
            "certification": dict(compiled.certification),
            "authored_map_blueprint_count": len(requirements),
            "approved_image_binding_count": len(asset_bindings),
            "starter_map_definition_count": len(stored_starter_maps),
            "starter_map_binding_count": len(compiled.world_release.map_bindings),
        }
        plan = {**dict(run.get("plan") or {}), "publication": publication_payload}
        progress = {
            **dict(run.get("progress") or {}),
            "publication": publication_payload,
            "artifact_stage": compiled.world_release.artifact_stage,
            "percent": 100,
        }
        updated = work.world_generation.update(
            context,
            run_id=run_id,
            status="ready",
            plan=plan,
            progress=progress,
            error={},
        )
        work.commit()
    return {
        "ok": True,
        "status": "ready",
        "run": updated,
        "publication": publication_payload,
        "reused": False,
    }


__all__ = ["publish_certified_world_generation"]
