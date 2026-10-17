"""Compile completed reusable-world topic runs into immutable revisions and releases."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.apps.rpg.genesis.forge.canon_audit import audit_generated_canon
from app.apps.rpg.genesis.forge.canon_compiler import compile_campaign_bible
from app.apps.rpg.genesis.forge.canon_relationships import compile_cross_domain_relationships
from app.apps.rpg.genesis.forge.world_forge_contract import (
    CampaignTopicGraph,
    CampaignTopicNode,
)
from app.apps.rpg.genesis.forge.world_forge_generation import (
    GeneratedTopic,
    WorldForgeGenerationResult,
    WorldForgeJobRecord,
)
from app.apps.rpg.genesis.forge.world_forge_quality import apply_world_forge_quality_audit

from .canon_repair import repair_generation_contracts
from .contracts import WorldArtifactStage, WorldReleaseDocument, WorldRevisionDocument
from .lifecycle_service import require_world_writable
from .map_blueprint_authoring import (
    latest_ready_blueprint_requirements,
    materialize_generated_location_blueprints,
)
from .map_blueprint_publication import merge_authored_blueprints
from .runtime_seed import (
    compile_runtime_seed,
    compile_vertical_slice,
    run_player_absent_playtest,
)
from .service import compile_world_release, compile_world_revision
from .world_image_bindings import approved_world_asset_bindings

_NON_GENERATION_CATEGORIES = {"compiler", "audit", "index", "bootstrap"}


@dataclass(frozen=True)
class WorldGenerationPublication:
    world_revision: WorldRevisionDocument
    world_release: WorldReleaseDocument
    certification: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "world_revision": self.world_revision.model_dump(mode="json"),
            "world_release": self.world_release.model_dump(mode="json"),
            "certification": dict(self.certification),
        }


def _graph_from_payload(value: Mapping[str, Any]) -> CampaignTopicGraph:
    nodes = tuple(
        CampaignTopicNode(
            topic_id=str(row.get("topic_id") or ""),
            title=str(row.get("title") or ""),
            category=str(row.get("category") or "lore"),
            dependencies=tuple(str(item) for item in row.get("dependencies") or ()),
            generator_role=str(row.get("generator_role") or "world_forge"),
            required_before_launch=bool(row.get("required_before_launch", True)),
            visibility=str(row.get("visibility") or "game_master_canon"),
            target_count=int(row.get("target_count") or 1),
            metadata=dict(row.get("metadata") or {}),
        )
        for row in value.get("nodes") or ()
        if isinstance(row, Mapping)
    )
    graph = CampaignTopicGraph(
        graph_version=str(value.get("graph_version") or "rpg_world_topic_graph_v1"),
        campaign_template=str(value.get("campaign_template") or "classic_fantasy"),
        depth=str(value.get("depth") or "standard"),  # type: ignore[arg-type]
        nodes=nodes,
        metadata=dict(value.get("metadata") or {}),
    )
    issues = graph.validate()
    if issues:
        raise ValueError("invalid_world_generation_graph:" + ",".join(issues))
    return graph


def _generation_result(
    graph: CampaignTopicGraph,
    topic_rows: list[Mapping[str, Any]],
) -> WorldForgeGenerationResult:
    rows = {str(row.get("topic_id") or ""): row for row in topic_rows}
    topics: list[GeneratedTopic] = []
    jobs: list[WorldForgeJobRecord] = []
    generation_order: list[tuple[str, ...]] = []
    missing: list[str] = []
    for node in graph.topological_order():
        if node.category in _NON_GENERATION_CATEGORIES:
            continue
        row = rows.get(node.topic_id)
        if row is None or str(row.get("status") or "") != "ready":
            missing.append(node.topic_id)
            continue
        topic = GeneratedTopic.from_dict(dict(row.get("content") or {}))
        if topic.topic_id != node.topic_id:
            raise ValueError(
                f"world_generation_topic_identity_mismatch:{node.topic_id}:{topic.topic_id}"
            )
        topics.append(topic)
        jobs.append(
            WorldForgeJobRecord(
                topic_id=node.topic_id,
                status="completed",
                dependency_ids=node.dependencies,
                generator_role=node.generator_role,
                output_counts={
                    "documents": len(topic.documents),
                    "entities": len(topic.entities),
                    "facts": len(topic.facts),
                    "relationships": len(topic.relationships),
                    "knowledge_rules": len(topic.knowledge_rules),
                    "story_threads": len(topic.story_threads),
                },
            )
        )
        generation_order.append((node.topic_id,))
    if missing:
        raise ValueError(
            "world_generation_topics_incomplete:" + ",".join(sorted(missing))
        )
    return WorldForgeGenerationResult(
        topics=tuple(topics),
        jobs=tuple(jobs),
        failed_topic_ids=(),
        generation_order=tuple(generation_order),
    )


def _profile_place_kinds(graph: CampaignTopicGraph) -> set[str]:
    profile = dict(graph.metadata.get("resolved_profile") or {})
    return {
        str(domain.get("entity_kind") or "")
        for domain in profile.get("domains") or ()
        if isinstance(domain, Mapping)
        and str(domain.get("domain_id") or "") == "places"
        and str(domain.get("entity_kind") or "")
    } or {"location"}


def _place_ids(canon: Mapping[str, Any], graph: CampaignTopicGraph) -> tuple[str, ...]:
    entities = raw_entities if isinstance(raw_entities := canon.get("entities"), Mapping) else {}
    kinds = _profile_place_kinds(graph)
    return tuple(
        str(entity_id)
        for entity_id, entity in sorted(dict(entities).items())
        if isinstance(entity, Mapping) and str(entity.get("kind") or "") in kinds
    )


def _topology(
    canon: Mapping[str, Any],
    graph: CampaignTopicGraph,
) -> dict[str, Any]:
    relationships = [
        dict(row)
        for row in canon.get("relationships") or ()
        if isinstance(row, Mapping)
        and str(row.get("kind") or row.get("type") or "").casefold()
        in {"route", "travel", "portal", "road", "path", "access_route"}
    ]
    return {
        "schema_version": "rpg_world_topology_v1",
        "locations": list(_place_ids(canon, graph)),
        "routes": relationships,
    }


def _blueprint_requirements(
    canon: Mapping[str, Any],
    graph: CampaignTopicGraph,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "map_id": f"map:{entity_id}",
            "location_id": entity_id,
            "simulation_readiness": "semantic",
            "presentation_readiness": "placeholder",
        }
        for entity_id in _place_ids(canon, graph)
    )


def _artifact_stage(
    *,
    runtime_seed_passed: bool,
    materialization_passed: bool,
    playtest_passed: bool,
) -> WorldArtifactStage:
    if playtest_passed:
        return "playtested"
    if materialization_passed:
        return "materialized"
    if runtime_seed_passed:
        return "runtime_seeded"
    return "canon_validated"


def compile_world_generation_publication(
    *,
    run: Mapping[str, Any],
    world: Mapping[str, Any],
    topic_rows: list[Mapping[str, Any]],
    revision: int,
    starting_location_override: str = "",
    asset_bindings: Mapping[str, Any] | None = None,
) -> WorldGenerationPublication:
    """Build deterministic immutable publication documents from one completed run."""

    graph = _graph_from_payload(dict(run.get("graph") or {}))
    generation = _generation_result(graph, topic_rows)
    context = dict(run.get("context") or {})
    generation_context = dict(context.get("generation_context") or {})
    starting_location = str(
        starting_location_override
        or generation_context.get("starting_location")
        or world.get("metadata", {}).get("starting_location")
        or ""
    )
    generation = repair_generation_contracts(
        generation,
        starting_location=starting_location,
        topic_graph=graph,
        generation_context=generation_context,
    )
    relationships = compile_cross_domain_relationships(generation.topics)
    audit = audit_generated_canon(
        generation.topics,
        compiled_relationships=relationships,
    )
    audit = apply_world_forge_quality_audit(generation.topics, audit)
    compilation = compile_campaign_bible(
        generation,
        compiled_relationships=relationships,
        audit=audit,
        topic_graph=graph.as_dict(),
        campaign_id=f"world-publication:{world['id']}:{revision}",
        campaign_template=graph.campaign_template,
        starting_location=starting_location,
        canon_revision=revision,
    )
    canon = dict(compilation.document)
    entity_manifest = {
        "schema_version": "rpg_world_entity_manifest_v1",
        "entities": dict(canon.get("entities") or {}),
        "manifest": dict(canon.get("manifest") or {}),
    }
    world_revision = compile_world_revision(
        world_id=str(world["id"]),
        revision=revision,
        title=str(world.get("title") or world["id"]),
        canon=canon,
        entity_manifest=entity_manifest,
        topology=_topology(canon, graph),
        adventure_seeds=tuple(
            dict(row)
            for row in canon.get("story_threads") or ()
            if isinstance(row, Mapping)
        ),
        blueprint_requirements=_blueprint_requirements(canon, graph),
        provenance={
            "source": "durable_world_generation",
            "generation_run_id": str(run.get("run_id") or ""),
            "settings": dict(run.get("settings") or {}),
            "topic_hashes": {
                str(row.get("topic_id") or ""): str(row.get("content_hash") or "")
                for row in topic_rows
            },
        },
    )
    runtime_seed = compile_runtime_seed(
        world_id=world_revision.world_id,
        world_revision=world_revision.revision,
        source_canon_hash=world_revision.content_hash,
        canon=canon,
        seed=int(world.get("seed") or 0),
    )
    materialization = compile_vertical_slice(
        runtime_seed=runtime_seed,
        canon=canon,
        starting_location=starting_location,
    )
    playtest = run_player_absent_playtest(runtime_seed, days=7)
    stage = _artifact_stage(
        runtime_seed_passed=runtime_seed.passed,
        materialization_passed=materialization.passed,
        playtest_passed=playtest.passed,
    )
    missing = list(compilation.missing_requirements)
    if not runtime_seed.passed:
        missing.append("runtime_seed")
    if not materialization.passed:
        missing.append("vertical_slice_materialization")
    if not playtest.passed:
        missing.append("player_absent_playtest")
    launch_ready = compilation.launch_ready and not missing
    artifact_readiness = {
        "canon_validated": audit.passed,
        "runtime_seeded": runtime_seed.passed,
        "materialized": materialization.passed,
        "playtested": playtest.passed,
        "highest_stage": stage,
    }
    certification = {
        "schema_version": "rpg_world_release_certification_v2",
        "launch_ready": launch_ready,
        "missing_requirements": list(dict.fromkeys(missing)),
        "completeness": dict(compilation.completeness),
        "consistency_report": audit.as_dict(),
        "artifact_readiness": artifact_readiness,
        "runtime_seed_hash": runtime_seed.content_hash,
        "materialization_hash": materialization.content_hash,
        "playtest_report_hash": playtest.content_hash,
        "generation_run_id": str(run.get("run_id") or ""),
        "draft_revision": int(run.get("draft_revision") or 1),
    }
    world_release = compile_world_release(
        world_revision,
        release=1,
        indexes=dict(compilation.retrieval_index),
        asset_bindings=dict(asset_bindings or {}),
        compiler_provenance={
            "compiler": "rpg_world_generation_publication_v2",
            "generation_run_id": str(run.get("run_id") or ""),
        },
        certification=certification,
        artifact_stage=stage,
        runtime_seed=runtime_seed.model_dump(mode="json"),
        materialization=materialization.model_dump(mode="json"),
        playtest_report=playtest.model_dump(mode="json"),
    )
    return WorldGenerationPublication(
        world_revision=world_revision,
        world_release=world_release,
        certification=certification,
    )


def publish_world_generation(
    run_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    """Atomically publish a completed run and make retries return the same release."""

    from app.security.tenant_context import current_tenant
    from app.persistence.unit_of_work import unit_of_work

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
        if str(run.get("status") or "") != "review":
            raise ValueError(
                f"world_generation_not_publishable:{run_id}:{run.get('status')}"
            )
        world_id = str(run.get("world_id") or "")
        world = require_world_writable(work, context, world_id)
        topic_rows = work.world_generation.list_topics(
            context,
            world_id=world_id,
            draft_revision=int(run.get("draft_revision") or 1),
        )
        current_row = work.connection.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM omnix_rpg_world_revisions "
            "WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        ).fetchone()
        current_revision = int(current_row[0])
        asset_bindings = approved_world_asset_bindings(work, context, world_id)
        compiled = compile_world_generation_publication(
            run=run,
            world=world,
            topic_rows=topic_rows,
            revision=current_revision + 1,
            asset_bindings=asset_bindings,
        )
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
        compiled = WorldGenerationPublication(
            world_revision=revision_document,
            world_release=release_document,
            certification=dict(release_document.certification),
        )
        stored_revision = work.world_scenarios.publish_world_revision(
            context,
            world_id=world_id,
            document=compiled.world_revision.model_dump(mode="json"),
            content_hash=compiled.world_revision.content_hash,
            expected_revision=current_revision,
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
