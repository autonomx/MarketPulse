"""Application services for the RPG Worlds & Campaigns library."""
from __future__ import annotations

from typing import Any, Mapping

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.forge.world_forge_profile_generation import (
    ProfileResolution,
    resolve_or_generate_genre_profile,
)
from app.apps.rpg.genesis.forge.world_forge_profile_graph import build_profile_topic_graph

from .generation_coordinator import reconcile_world_generation, start_world_generation
from .generation_jobs import WorldTopicGenerationSettings, canonical_hash
from .generation_publication_guard import publish_world_generation
from .generation_routing import resolve_world_forge_route
from .generation_scope import resolve_generation_scope
from .generation_worker import kick_world_generation_worker
from .lifecycle_service import require_world_writable
from .map_blueprint_authoring import list_map_blueprints
from .profile_authoring import require_approved_profile
from .profile_generation_jobs import profile_manifest_run, profile_resolution_from_world


def _database(value: Any | None) -> Any | None:
    return value


def _world_generation_context(
    world: Mapping[str, Any],
    *,
    starting_location: str,
    background_expansion: bool,
    route: Any,
    profile_resolution: ProfileResolution | None = None,
    approved_profile_hash: str = "",
) -> dict[str, Any]:
    """Build a durable, authoritative setting brief for every topic request."""

    metadata = dict(world.get("metadata") or {})
    genre = str(world.get("genre") or "classic_fantasy")
    tone = str(world.get("tone") or "heroic adventure")
    context: dict[str, Any] = {
        "world_brief": {
            "title": str(world.get("title") or "").strip(),
            "description": str(world.get("description") or "").strip(),
            "source_mode": str(world.get("source_mode") or "").strip(),
            "genre": genre,
            "tone": tone,
            "campaign_template": str(metadata.get("campaign_template") or "").strip(),
        },
        "genre": genre,
        "tone": tone,
        "starting_location": starting_location,
        "background_expansion": background_expansion,
        "requested_provider_route": route.requested_provider,
        "requested_model": route.requested_model,
        "resolved_provider_source": route.source,
        "approved_profile_hash": approved_profile_hash,
    }
    if profile_resolution is not None:
        context["genre_profile_resolution"] = profile_resolution.as_dict()
        context["resolved_genre_profile"] = profile_resolution.profile.as_dict()
        context["resolved_profile_hash"] = profile_resolution.profile.content_hash
    return context


def read_world_library(
    *,
    database: Any | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        worlds = work.world_scenarios.list_worlds(context, limit=limit)
        scenarios = work.world_library.list_scenarios(context, limit=limit * 2)
        campaigns = work.world_library.list_campaign_bindings(context, limit=limit * 2)
        runs = work.world_library.list_generation_runs(context, limit=limit * 2)
        # Per-world figures come from the database, not from the pages above,
        # so a world outside the newest pages still shows its own (WP-5.5).
        world_ids = [str(world["id"]) for world in worlds]
        scenario_counts = work.world_library.published_scenario_counts(context, world_ids)
        latest_runs = work.world_library.latest_generation_runs(context, world_ids)
        work.rollback()

    summaries = []
    for world in worlds:
        world_id = str(world["id"])
        summaries.append(
            {
                **world,
                "scenario_count": scenario_counts.get(world_id, 0),
                "generation": latest_runs.get(world_id),
            }
        )
    return {
        "ok": True,
        "worlds": summaries,
        "scenarios": scenarios,
        "campaigns": campaigns,
        "generation_runs": runs,
    }


def read_world_detail(
    world_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world = work.world_scenarios.get_world(context, world_id)
        if world is None:
            work.rollback()
            raise KeyError(f"world_not_found:{world_id}")
        topics = work.world_library.list_topics(context, world_id)
        revisions = work.world_library.list_world_revisions(context, world_id)
        releases = work.world_library.list_world_releases(context, world_id)
        scenarios = list(work.world_library.iter_scenarios(context, world_id=world_id))
        runs = list(work.world_library.iter_generation_runs(context, world_id=world_id))
        generation_topic_results = {
            str(run["run_id"]): work.world_generation.list_topic_results(
                context,
                run_id=str(run["run_id"]),
            )
            for run in runs
        }
        scenario_revisions = {
            scenario["id"]: work.world_library.list_scenario_revisions(
                context,
                str(scenario["id"]),
            )
            for scenario in scenarios
        }
        work.rollback()
    if not runs:
        manifest_run = profile_manifest_run(world)
        if manifest_run is not None:
            runs = [manifest_run]
    return {
        "ok": True,
        "world": world,
        "topics": topics,
        "map_blueprints": list_map_blueprints(world_id, database=database),
        "revisions": revisions,
        "releases": releases,
        "scenarios": scenarios,
        "scenario_revisions": scenario_revisions,
        "generation_runs": runs,
        "generation_topic_results": generation_topic_results,
    }


def save_world_topic(
    world_id: str,
    *,
    topic_id: str,
    content: Mapping[str, Any],
    directives: Mapping[str, Any] | None = None,
    status: str = "ready",
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world = require_world_writable(work, context, world_id)
        payload = dict(content)
        payload.setdefault("topic_id", topic_id)
        stored = work.world_scenarios.put_topic(
            context,
            world_id=world_id,
            topic_id=topic_id,
            draft_revision=int(world["draft_revision"]),
            source="manual",
            status=status,
            content=payload,
            directives=dict(directives or {}),
            dependency_hashes={},
            input_hash=canonical_hash(
                {
                    "topic_id": topic_id,
                    "content": payload,
                    "directives": dict(directives or {}),
                }
            ),
            content_hash=canonical_hash(payload),
            provenance={"source": "world_library_manual_authoring"},
        )
        work.commit()
    return {"ok": True, "topic": stored}


def _execution_summary(run: Mapping[str, Any]) -> dict[str, Any]:
    plan = dict(run.get("plan") or {})
    progress = dict(run.get("progress") or {})
    queued_jobs = list(plan.get("new_job_ids") or ())
    active_topics = list(progress.get("active_topic_ids") or ())
    return {
        "queued_topic_ids": active_topics,
        "queued_job_ids": queued_jobs,
        "reused_topic_ids": list(plan.get("reusable_topic_ids") or ()),
        "protected_topic_ids": list(plan.get("protected_topic_ids") or ()),
        "target_topic_ids": list(plan.get("topic_ids") or ()),
        "queued_count": len(queued_jobs),
        "reused_count": len(plan.get("reusable_topic_ids") or ()),
        "protected_count": len(plan.get("protected_topic_ids") or ()),
        "active_count": len(active_topics),
        "accepted_count": len(progress.get("accepted_topic_ids") or ()),
        "flagged_count": len(progress.get("flagged_topic_ids") or ()),
        "failed_count": len(progress.get("failed_topic_ids") or ()),
        "blocked_count": len(progress.get("blocked_topic_ids") or ()),
    }


def start_world_library_generation(
    world_id: str,
    *,
    depth: str = "standard",
    starting_location: str = "",
    background_expansion: bool = True,
    topic_directives: Mapping[str, Mapping[str, Any]] | None = None,
    entity_manifest: Mapping[str, Any] | None = None,
    scope: Mapping[str, Any] | None = None,
    strategy: str = "reuse_unchanged",
    replace_locked: bool = False,
    generator_version: str = "world-generator-v1",
    prompt_version: str = "world-prompt-v1",
    provider_route: str = "configured",
    model: str = "configured",
    database: Any | None = None,
    kick_worker: bool = True,
) -> dict[str, Any]:
    if strategy not in {"reuse_unchanged", "force"}:
        raise ValueError(f"invalid_generation_strategy:{strategy}")
    context = current_tenant()
    with unit_of_work(database) as work:
        world = require_world_writable(work, context, world_id)
        topics = work.world_library.list_topics(context, world_id)
        runs = work.world_library.list_generation_runs(
            context,
            world_id=world_id,
            limit=1,
        )
        work.rollback()

    profile_review = require_approved_profile(world)
    route = resolve_world_forge_route(provider_route, model)
    metadata = dict(world.get("metadata") or {})
    campaign_template = str(metadata.get("campaign_template") or "classic_fantasy")
    profile_resolution = profile_resolution_from_world(world)
    if profile_resolution is None:
        profile_resolution = resolve_or_generate_genre_profile(
            genre=str(world.get("genre") or campaign_template),
            description=str(world.get("description") or ""),
            campaign_mode=str(
                metadata.get("campaign_mode") or "persistent_living_world"
            ),
        )
    if profile_resolution.profile.content_hash != str(profile_review["approved_profile_hash"]):
        raise ValueError("world_profile_approval_hash_mismatch")
    graph = build_profile_topic_graph(
        profile_resolution.profile,
        campaign_template=campaign_template,
        depth=depth,
        tone=str(world.get("tone") or ""),
        starting_location=starting_location,
        background_expansion=background_expansion,
        runtime_capabilities=dict(metadata.get("runtime_capabilities") or {}),
    )

    target_topic_ids, forced_topic_ids, normalized_scope = resolve_generation_scope(
        graph,
        scope=scope,
        strategy=strategy,
        topic_rows=topics,
        latest_run=runs[0] if runs else None,
        replace_locked=replace_locked,
    )
    generation_context = _world_generation_context(
        world,
        starting_location=starting_location,
        background_expansion=background_expansion,
        route=route,
        profile_resolution=profile_resolution,
        approved_profile_hash=str(profile_review["approved_profile_hash"]),
    )
    run = start_world_generation(
        world_id=world_id,
        draft_revision=int(world["draft_revision"]),
        graph=graph,
        generation_context=generation_context,
        topic_directives=dict(topic_directives or {}),
        entity_manifest_hash=canonical_hash(dict(entity_manifest or {})),
        settings=WorldTopicGenerationSettings(
            generator_version=generator_version,
            prompt_version=prompt_version,
            provider_route=route.provider,
            model=route.model,
            seed=int(world.get("seed") or 0),
        ),
        target_topic_ids=target_topic_ids,
        forced_topic_ids=forced_topic_ids,
        scope=normalized_scope,
        strategy=strategy,
        database=database,
        tenant_context=context,
    )
    worker_started = (
        kick_world_generation_worker(
            database=database,
            provider_route=route.provider,
        )
        if kick_worker
        else False
    )
    return {
        "ok": True,
        "run": run,
        "worker_started": worker_started,
        "scope": normalized_scope,
        "execution_summary": _execution_summary(run),
        "resolved_route": {
            "provider": route.provider,
            "model": route.model,
            "source": route.source,
        },
        "genre_profile": profile_resolution.as_dict(),
        "approved_profile_hash": profile_review["approved_profile_hash"],
    }


def read_world_generation(
    run_id: str,
    *,
    database: Any | None = None,
    reconcile: bool = True,
) -> dict[str, Any]:
    if reconcile:
        run = reconcile_world_generation(run_id, database=database)
    else:
        context = current_tenant()
        with unit_of_work(database) as work:
            run = work.world_generation.get(context, run_id)
            work.rollback()
        if run is None:
            raise KeyError(f"world_generation_run_not_found:{run_id}")
    if str(run.get("status") or "") in {"planned", "running"}:
        settings = dict(run.get("settings") or {})
        kick_world_generation_worker(
            database=database,
            provider_route=str(settings.get("provider_route") or ""),
        )
    context = current_tenant()
    with unit_of_work(database) as work:
        topic_results = work.world_generation.list_topic_results(
            context,
            run_id=run_id,
        )
        work.rollback()
    return {"ok": True, "run": run, "topic_results": topic_results}


def publish_world_library_generation(
    run_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        run = work.world_generation.get(context, run_id)
        if run is None:
            raise KeyError(f"world_generation_run_not_found:{run_id}")
        require_world_writable(work, context, str(run["world_id"]))
        work.rollback()
    return publish_world_generation(run_id, database=database)
