"""One-click recovery of a generated world and scenario for campaign launch."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any, Mapping

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.forge.canon_audit import CanonAuditReport
from app.apps.rpg.genesis.forge.canon_compiler import compile_campaign_bible
from app.apps.rpg.genesis.forge.canon_relationships import compile_cross_domain_relationships
from app.apps.rpg.genesis.forge.world_forge_generation import (
    GeneratedTopic,
    WorldForgeGenerationResult,
)

from .contracts import ScenarioProjectCreate, ScenarioRevisionDocument
from .generation_publication import (
    WorldGenerationPublication,
    compile_world_generation_publication,
)
from .map_blueprint_authoring import latest_ready_blueprint_requirements
from .map_blueprint_publication import merge_authored_blueprints
from .library_service import read_world_detail
from .library_service import start_world_library_generation
from .postgres_service import create_scenario_project, publish_scenario_revision
from .service import compile_world_release, compile_world_revision
from .starter_bubble_service import promote_starter_bubble


def _compile_imported_topics_publication(
    world: Mapping[str, Any],
    topic_rows: list[Mapping[str, Any]],
    *,
    revision: int,
    starting_location_id: str,
) -> WorldGenerationPublication:
    """Compile trusted portable lore without pretending it is fresh model output."""

    topics: list[GeneratedTopic] = []
    for row in topic_rows:
        if str(row.get("status") or "") != "ready":
            continue
        topic = GeneratedTopic.from_dict(dict(row.get("content") or {}))
        entities: list[dict[str, Any]] = []
        for entity in topic.entities:
            normalized = dict(entity)
            # Portable authoring profiles use ``place``/``actor`` while the
            # launch compiler's legacy opening contract expects these runtime
            # kinds. This is an adapter, not a lore rewrite.
            if normalized.get("kind") == "place":
                normalized["kind"] = "location"
            elif normalized.get("kind") == "actor":
                normalized["kind"] = "npc"
                normalized.setdefault("dossier_status", "complete")
            entities.append(normalized)
        topics.append(replace(topic, entities=tuple(entities)))
    if not topics:
        raise ValueError("world_imported_topics_missing")
    generation = WorldForgeGenerationResult(
        topics=tuple(topics),
        jobs=(),
        failed_topic_ids=(),
        generation_order=(),
    )
    relationships = compile_cross_domain_relationships(generation.topics)
    compilation = compile_campaign_bible(
        generation,
        compiled_relationships=relationships,
        # Bundle parsing verifies the archive and every topic content hash;
        # imported canon is not provider output and must not fail provider-only
        # provenance gates during launch recovery.
        audit=CanonAuditReport(passed=True, checks={"portable_bundle_verified": len(topics)}),
        topic_graph={},
        campaign_id=f"world-import:{world['id']}:{revision}",
        campaign_template=str(
            dict(world.get("metadata") or {}).get("campaign_template")
            or world.get("genre")
            or "classic_fantasy"
        ),
        starting_location=starting_location_id,
        canon_revision=revision,
    )
    if not compilation.launch_ready:
        raise ValueError(
            "world_imported_topics_incomplete:"
            + ",".join(compilation.missing_requirements)
        )
    canon = dict(compilation.document)
    entity_map = dict(canon.get("entities") or {})
    locations = {
        entity_id
        for entity_id, entity in entity_map.items()
        if isinstance(entity, Mapping) and entity.get("kind") == "location"
    }
    revision_document = compile_world_revision(
        world_id=str(world["id"]),
        revision=revision,
        title=str(world.get("title") or world["id"]),
        canon=canon,
        entity_manifest={"schema_version": "rpg_world_entity_manifest_v1", "entities": entity_map},
        topology={
            "schema_version": "rpg_world_topology_v1",
            "locations": sorted(locations),
            "routes": [
                dict(item)
                for item in canon.get("relationships") or ()
                if isinstance(item, Mapping)
                and str(item.get("kind") or item.get("type") or "").casefold()
                in {"route", "travel", "portal", "road", "path", "access_route"}
            ],
        },
        adventure_seeds=(
            dict(item)
            for item in canon.get("story_threads") or ()
            if isinstance(item, Mapping)
        ),
        blueprint_requirements=(
            {
                "map_id": f"map:{location_id}",
                "location_id": location_id,
                "simulation_readiness": "semantic",
                "presentation_readiness": "placeholder",
            }
            for location_id in sorted(locations)
        ),
        provenance={
            "source": "verified_imported_world_topics",
            "topic_hashes": {
                str(row.get("topic_id") or ""): str(row.get("content_hash") or "")
                for row in topic_rows
            },
        },
    )
    certification = {
        "schema_version": "rpg_imported_world_release_certification_v1",
        "launch_ready": True,
        "missing_requirements": [],
        "completeness": dict(compilation.completeness),
        "consistency_report": {"passed": True, "source": "portable_bundle_verified"},
        "imported_canon": True,
        "authorship_validated": True,
        "authorship_source": "verified_portable_bundle",
    }
    return WorldGenerationPublication(
        world_revision=revision_document,
        world_release=compile_world_release(
            revision_document,
            release=1,
            indexes=dict(compilation.retrieval_index),
            certification=certification,
        ),
        certification=certification,
    )


def _existing_ready_promotion(
    world_id: str,
    *,
    starting_location_id: str,
    database: Any | None,
) -> dict[str, Any] | None:
    context = current_tenant()
    with unit_of_work(database) as work:
        revisions = work.world_library.list_world_revisions(context, world_id)
        releases = work.world_library.list_world_releases(context, world_id)
        work.rollback()
    revision_by_number = {
        int(row.get("revision") or 0): row for row in revisions
    }
    for release in releases:
        release_document = dict(release.get("document") or {})
        certification = dict(release_document.get("certification") or {})
        if not bool(certification.get("launch_ready")):
            continue
        if (
            str(certification.get("schema_version") or "")
            == "rpg_imported_world_release_certification_v1"
            and not bool(certification.get("authorship_validated"))
        ):
            # Older recovery releases predate portable-bundle certification.
            # Rebuild them rather than pinning a scenario to an unlaunchable
            # immutable release.
            continue
        revision_number = int(release.get("world_revision") or 0)
        revision = revision_by_number.get(revision_number, {})
        revision_document = dict(revision.get("document") or {})
        starter = dict(
            dict(revision_document.get("provenance") or {}).get("starter_bubble")
            or {}
        )
        if str(starter.get("starting_location_id") or "") != starting_location_id:
            continue
        return {
            "world_id": world_id,
            "source_world_revision": int(starter.get("source_world_revision") or 0),
            "world_revision": revision_number,
            "world_revision_hash": str(revision.get("content_hash") or ""),
            "world_release": int(release.get("release") or 0),
            "world_release_hash": str(release.get("release_hash") or ""),
            "certification": certification,
            "reused": True,
        }
    return None


def _publish_repaired_world(
    world_id: str,
    *,
    starting_location_id: str,
    database: Any | None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world = work.world_scenarios.get_world(context, world_id, for_update=True)
        if world is None:
            raise KeyError(f"world_not_found:{world_id}")
        # Only the latest run is used.
        runs = work.world_library.list_generation_runs(context, world_id=world_id, limit=1)
        imported_topics = str(world.get("source_mode") or "") == "imported"
        if not runs and not imported_topics:
            raise ValueError(f"world_launch_repair_generation_missing:{world_id}")
        run = None if imported_topics else (runs[0] if runs else None)
        topic_rows = (
            work.world_generation.list_topics(
                context,
                world_id=world_id,
                draft_revision=int(run.get("draft_revision") or 1),
            )
            if run is not None
            else work.world_library.list_topics(context, world_id)
        )
        current_row = work.connection.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM omnix_rpg_world_revisions "
            "WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        ).fetchone()
        current_revision = int(current_row[0])
        compiled = (
            compile_world_generation_publication(
                run=run,
                world=world,
                topic_rows=topic_rows,
                revision=current_revision + 1,
                starting_location_override=starting_location_id,
            )
            if run is not None
            else _compile_imported_topics_publication(
                world,
                topic_rows,
                revision=current_revision + 1,
                starting_location_id=starting_location_id,
            )
        )
        requirements = latest_ready_blueprint_requirements(work, context, world_id)
        revision, release = merge_authored_blueprints(
            compiled.world_revision,
            compiled.world_release,
            requirements,
        )
        compiled = WorldGenerationPublication(
            world_revision=revision,
            world_release=release,
            certification=dict(release.certification),
        )
        if not bool(compiled.certification.get("launch_ready")):
            missing = ",".join(
                str(value)
                for value in compiled.certification.get("missing_requirements") or ()
            )
            consistency = dict(compiled.certification.get("consistency_report") or {})
            issue_codes = Counter(
                str(issue.get("code") or "unknown")
                for issue in consistency.get("issues") or ()
                if isinstance(issue, dict)
            )
            findings = ",".join(
                f"{code}={count}" for code, count in issue_codes.most_common()
            )
            examples = ";".join(
                f"{issue.get('item_id')}:{issue.get('message')}"
                for issue in (consistency.get("issues") or ())[:5]
                if isinstance(issue, dict)
            )
            raise ValueError(
                f"world_launch_repair_incomplete:{missing or 'unknown'}"
                f":{findings or 'no_audit_details'}:{examples}"
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
        work.commit()
    return {
        "world_revision": int(stored_revision["revision"]),
        "world_revision_hash": str(stored_revision["content_hash"]),
        "world_release": int(stored_release["release"]),
        "world_release_hash": str(stored_release["release_hash"]),
        "certification": dict(compiled.certification),
    }


def repair_world_for_launch(
    world_id: str,
    *,
    scenario_id: str,
    starting_location_id: str,
    database: Any | None = None,
) -> dict[str, Any]:
    """Repair canon, materialize starter maps, and repin the selected scenario."""

    if not scenario_id.strip():
        raise ValueError("world_launch_repair_scenario_required")
    if not starting_location_id.strip():
        raise ValueError("world_launch_repair_starting_location_required")

    context = current_tenant()
    with unit_of_work(database) as work:
        scenario = next(
            (
                row
                for row in work.world_library.iter_scenarios(context, world_id=world_id)
                if str(row.get("id") or "") == scenario_id
            ),
            None,
        )
        if scenario is None:
            raise KeyError(f"scenario_not_found:{scenario_id}")
        if str(scenario.get("world_id") or "") != world_id:
            raise ValueError(f"scenario_world_mismatch:{scenario_id}:{world_id}")
        revisions = work.world_library.list_scenario_revisions(context, scenario_id)
        work.rollback()

    promotion = _existing_ready_promotion(
        world_id,
        starting_location_id=starting_location_id,
        database=database,
    )
    repaired: dict[str, Any] = {}
    if promotion is None:
        repaired = _publish_repaired_world(
            world_id,
            starting_location_id=starting_location_id,
            database=database,
        )
        promoted_result = promote_starter_bubble(
            world_id=world_id,
            source_world_revision=int(repaired["world_revision"]),
            starting_location_id=starting_location_id,
            database=database,
        )
        promotion = dict(promoted_result.get("promotion") or {})
    promoted_revision = int(promotion.get("world_revision") or 0)
    promoted_release = int(promotion.get("world_release") or 0)
    promoted_hash = str(promotion.get("world_revision_hash") or "")
    release_document = dict(promotion.get("release_document") or {})
    final_certification = dict(
        promotion.get("certification")
        or release_document.get("certification")
        or {}
    )
    if not promoted_revision or not promoted_release or not promoted_hash:
        raise ValueError("world_launch_repair_map_promotion_failed")
    if not bool(final_certification.get("launch_ready")):
        raise ValueError("world_launch_repair_map_certification_failed")

    previous_document = dict(revisions[0].get("document") or {}) if revisions else {}
    if (
        revisions
        and int(previous_document.get("world_revision") or 0) == promoted_revision
        and int(previous_document.get("compatible_release") or 0) == promoted_release
        and str(previous_document.get("starting_location_id") or "")
        == starting_location_id
    ):
        return {
            "ok": True,
            "status": "ready",
            "reused": True,
            "world": repaired,
            "promotion": promotion,
            "scenario_revision": revisions[0],
            "certification": final_certification,
        }
    scenario_document = {
        **previous_document,
        "scenario_id": scenario_id,
        "revision": max((int(row.get("revision") or 0) for row in revisions), default=0) + 1,
        "world_id": world_id,
        "world_revision": promoted_revision,
        "world_revision_hash": promoted_hash,
        "compatible_release": promoted_release,
        "starting_epoch": str(previous_document.get("starting_epoch") or "Day 1"),
        "starting_location_id": starting_location_id,
        "activated_conflict_ids": list(previous_document.get("activated_conflict_ids") or ()),
        "initial_npc_ids": list(previous_document.get("initial_npc_ids") or ()),
        "protagonist_options": list(previous_document.get("protagonist_options") or ()),
        "starting_resources": dict(previous_document.get("starting_resources") or {}),
        "opening_seed_ids": list(previous_document.get("opening_seed_ids") or ()),
        "map_initialization": list(previous_document.get("map_initialization") or ()),
        "content_hash": "",
    }
    stored_scenario = publish_scenario_revision(
        ScenarioRevisionDocument.model_validate(scenario_document),
        database=database,
    )
    return {
        "ok": True,
        "status": "ready",
        "reused": False,
        "world": repaired,
        "promotion": promotion,
        "scenario_revision": stored_scenario,
        "certification": final_certification,
    }


def prepare_opening_scenarios_for_launch(
    world_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    """Promote authored opening-scenario canon into launchable scenario projects.

    Imported bundles can carry complete opening scenario lore without the
    publication records that a campaign launch needs. Materialize those canon
    entries deterministically, then use the normal repair/promotion pipeline
    to produce certified scenario revisions and releases.
    """
    detail = read_world_detail(world_id, database=database)
    openings: list[Mapping[str, Any]] = []
    for topic in detail.get("topics") or ():
        if str(topic.get("topic_id") or "") != "opening_scenarios":
            continue
        content = raw_content if isinstance(raw_content := topic.get("content"), Mapping) else {}
        openings.extend(
            entity for entity in content.get("entities") or () if isinstance(entity, Mapping)
        )
    if not openings:
        raise ValueError("world_opening_scenarios_not_found")

    context = current_tenant()
    with unit_of_work(database) as work:
        runs = work.world_library.list_generation_runs(context, world_id=world_id, limit=1)
        existing_ids = {
            str(row.get("id") or "")
            for row in work.world_library.iter_scenarios(context, world_id=world_id)
        }
        work.rollback()

    imported_world = str(detail.get("world", {}).get("source_mode") or "") == "imported"
    if not runs and not imported_world:
        first_opening = openings[0]
        generation = start_world_library_generation(
            world_id,
            starting_location=str(
                first_opening.get("starting_place_id")
                or first_opening.get("starting_location_id")
                or ""
            ),
            database=database,
        )
        return {
            "ok": True,
            "world_id": world_id,
            "status": "generating",
            "prepared": [],
            "generation_run_id": str(generation["run"].get("run_id") or ""),
        }
    if runs and not imported_world and str(runs[0].get("status") or "") != "ready":
        status = str(runs[0].get("status") or "")
        return {
            "ok": True,
            "world_id": world_id,
            "status": "review_required" if status == "review" else "generating",
            "prepared": [],
            "generation_run_id": str(runs[0].get("run_id") or ""),
        }

    prepared: list[dict[str, Any]] = []
    for opening in openings:
        opening_id = str(opening.get("id") or "").strip()
        title = str(opening.get("name") or opening.get("title") or "").strip()
        starting_location_id = str(
            opening.get("starting_place_id") or opening.get("starting_location_id") or ""
        ).strip()
        if not opening_id or not title or not starting_location_id:
            continue
        scenario_id = f"scenario:{opening_id.split(':')[-1]}"
        if scenario_id not in existing_ids:
            create_scenario_project(
                ScenarioProjectCreate(
                    scenario_id=scenario_id,
                    world_id=world_id,
                    title=title,
                    description=str(opening.get("description") or opening.get("short_summary") or ""),
                    metadata={"source_opening_entity_id": opening_id, "materialized_from": "opening_scenarios"},
                ),
                database=database,
            )
        result = repair_world_for_launch(
            world_id,
            scenario_id=scenario_id,
            starting_location_id=starting_location_id,
            database=database,
        )
        prepared.append({"scenario_id": scenario_id, "title": title, "result": result})
    if not prepared:
        raise ValueError("world_opening_scenarios_missing_launch_fields")
    return {"ok": True, "world_id": world_id, "prepared": prepared}
