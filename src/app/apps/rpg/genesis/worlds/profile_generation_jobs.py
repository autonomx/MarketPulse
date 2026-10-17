"""Durable, world-owned genre-profile planning and execution."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, NoReturn

from app.jobs.models import ResourceClass
from app.persistence.database import DatabaseUnavailableError
from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.forge.world_forge_profile_generation import (
    GenreProfileGenerator,
    GenreProfileRegistry,
    ProfileResolution,
    default_profile_registry,
    resolve_or_generate_genre_profile,
)
from app.apps.rpg.genesis.forge.world_forge_profile_graph import build_profile_topic_graph
from app.apps.rpg.genesis.forge.world_forge_profile_provider import (
    build_genre_profile_generator_from_settings,
)
from app.apps.rpg.genesis.forge.world_forge_profiles import genre_profile_from_dict

from .generation_jobs import canonical_hash
from .generation_routing import ResolvedWorldForgeRoute, resolve_world_forge_route

WORLD_PROFILE_JOB_TYPE = "rpg.world.profile.generate"
WORLD_PROFILE_RESOURCE_CLASS = ResourceClass.RPG_WORLD_GENERATION.value
WORLD_PROFILE_JOB_CONTRACT = "rpg_world_profile_job_v1"
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.:-]+")


@dataclass(frozen=True)
class WorldProfileCreationPlan:
    binding: Mapping[str, Any]
    job_payload: Mapping[str, Any] | None
    provider_route: str


def _profile_input(
    *,
    title: str,
    genre: str,
    description: str,
    tone: str,
    campaign_mode: str,
) -> dict[str, str]:
    return {
        "title": str(title or "").strip(),
        "genre": str(genre or "").strip(),
        "description": str(description or "").strip(),
        "tone": str(tone or "").strip(),
        "campaign_mode": str(campaign_mode or "persistent_living_world").strip(),
    }


def _ready_binding(
    resolution: ProfileResolution,
    *,
    input_hash: str,
    route: ResolvedWorldForgeRoute | None = None,
    job_id: str = "",
) -> dict[str, Any]:
    return {
        "status": "ready",
        "requested_genre": resolution.requested_genre,
        "normalized_genre": resolution.normalized_genre,
        "source": resolution.source,
        "generated": resolution.generated,
        "profile_id": resolution.profile.profile_id,
        "profile_version": resolution.profile.version,
        "profile_hash": resolution.profile.content_hash,
        "profile": resolution.profile.as_dict(),
        "input_hash": input_hash,
        "job_id": job_id,
        "route": (
            {
                "provider": route.provider,
                "model": route.model,
                "source": route.source,
            }
            if route is not None
            else {"provider": "registry", "model": "", "source": "registry"}
        ),
        "error": {},
    }


def plan_world_profile_creation(
    *,
    world_id: str,
    title: str,
    genre: str,
    description: str,
    tone: str,
    campaign_mode: str,
    seed: int,
    route: ResolvedWorldForgeRoute | None = None,
) -> WorldProfileCreationPlan:
    """Resolve built-ins immediately and queue unknown genres for one LLM call."""

    profile_input = _profile_input(
        title=title,
        genre=genre,
        description=description,
        tone=tone,
        campaign_mode=campaign_mode,
    )
    input_hash = canonical_hash(profile_input)
    existing = default_profile_registry().resolve(genre)
    if existing is not None:
        resolution = ProfileResolution(
            profile=existing,
            source="registry",
            requested_genre=genre,
            normalized_genre=existing.profile_id,
            generated=False,
        )
        return WorldProfileCreationPlan(
            binding=_ready_binding(resolution, input_hash=input_hash),
            job_payload=None,
            provider_route="",
        )

    resolved_route = route or resolve_world_forge_route("configured", "configured")
    safe_world = _SAFE_ID.sub("-", world_id).strip("-")
    digest = hashlib.sha256(input_hash.encode("utf-8")).hexdigest()[:16]
    job_id = f"world-profile:{safe_world}:{digest}"
    settings = {
        "provider_route": resolved_route.provider,
        "model": resolved_route.model,
        "seed": int(seed),
        "max_attempts": 2,
    }
    job_payload = {
        "id": job_id,
        "module": "rpg",
        "job_type": WORLD_PROFILE_JOB_TYPE,
        "resource_class": WORLD_PROFILE_RESOURCE_CLASS,
        "priority": 5,
        "max_attempts": 2,
        "input_payload": {
            "contract_version": WORLD_PROFILE_JOB_CONTRACT,
            "world_id": world_id,
            "profile_input": profile_input,
            "profile_input_hash": input_hash,
            "settings": settings,
        },
        "metadata": {
            "contract_version": WORLD_PROFILE_JOB_CONTRACT,
            "world_id": world_id,
            "profile_input_hash": input_hash,
        },
    }
    binding = {
        "status": "generating",
        "requested_genre": genre,
        "normalized_genre": "",
        "source": "pending",
        "generated": True,
        "profile_id": "",
        "profile_version": 0,
        "profile_hash": "",
        "profile": {},
        "input_hash": input_hash,
        "job_id": job_id,
        "route": {
            "provider": resolved_route.provider,
            "model": resolved_route.model,
            "source": resolved_route.source,
        },
        "error": {},
    }
    return WorldProfileCreationPlan(
        binding=binding,
        job_payload=job_payload,
        provider_route=resolved_route.provider,
    )


def retry_world_profile_creation(
    world_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    """Queue a fresh profile-provider attempt after terminal validation failure."""

    context = current_tenant()
    with unit_of_work(database) as work:
        world = work.world_scenarios.get_world(context, world_id)
        if world is None:
            raise KeyError(f"world_not_found:{world_id}")
        existing = dict(
            dict(world.get("metadata") or {}).get("genre_profile_binding") or {}
        )
        if str(existing.get("status") or "") != "validation_failed":
            raise ValueError("world_profile_retry_not_available")
        route_data = dict(existing.get("route") or {})
        route = ResolvedWorldForgeRoute(
            provider=str(route_data.get("provider") or ""),
            model=str(route_data.get("model") or ""),
            source=str(route_data.get("source") or "profile_retry"),
            requested_provider=str(route_data.get("provider") or ""),
            requested_model=str(route_data.get("model") or ""),
        )
        metadata = dict(world.get("metadata") or {})
        plan = plan_world_profile_creation(
            world_id=world_id,
            title=str(world.get("title") or ""),
            genre=str(world.get("genre") or ""),
            description=str(world.get("description") or ""),
            tone=str(world.get("tone") or ""),
            campaign_mode=str(metadata.get("campaign_mode") or "persistent_living_world"),
            seed=int(world.get("seed") or 0),
            route=route,
        )
        if plan.job_payload is None:
            raise ValueError("world_profile_retry_provider_not_required")
        retry_count = int(existing.get("retry_count") or 0) + 1
        payload = dict(plan.job_payload)
        payload["id"] = f"{payload['id']}:retry:{retry_count}"
        payload["input_payload"] = {
            **dict(payload.get("input_payload") or {}),
            "retry_of_job_id": str(existing.get("job_id") or ""),
        }
        payload["metadata"] = {
            **dict(payload.get("metadata") or {}),
            "retry_count": retry_count,
        }
        binding = {
            **dict(plan.binding),
            "profile_revision": int(existing.get("profile_revision") or 1),
            "retry_count": retry_count,
            "retry_of_job_id": str(existing.get("job_id") or ""),
        }
        _set_binding(work, context, world=world, binding=binding)
        work.jobs.create_job(context, payload)
        work.commit()

    from .generation_worker import kick_world_generation_worker
    from .profile_authoring import profile_review_from_world

    kick_world_generation_worker(database=database, provider_route=plan.provider_route)
    return {
        "ok": True,
        "review": profile_review_from_world(
            {
                **dict(world),
                "metadata": {**metadata, "genre_profile_binding": binding},
            }
        ),
    }


def profile_resolution_from_world(
    world: Mapping[str, Any],
    *,
    allow_legacy_missing: bool = True,
) -> ProfileResolution | None:
    metadata = dict(world.get("metadata") or {})
    binding = dict(metadata.get("genre_profile_binding") or {})
    if not binding:
        return None if allow_legacy_missing else _raise_not_ready("unresolved")
    status = str(binding.get("status") or "unresolved")
    if status not in {"ready", "approved"}:
        return _raise_not_ready(status)
    profile = genre_profile_from_dict(dict(binding.get("profile") or {})).require_valid()
    if str(binding.get("profile_hash") or "") != profile.content_hash:
        raise ValueError("world_profile_hash_mismatch")
    return ProfileResolution(
        profile=profile,
        source=str(binding.get("source") or "world_binding"),
        requested_genre=str(binding.get("requested_genre") or world.get("genre") or ""),
        normalized_genre=str(binding.get("normalized_genre") or ""),
        generated=bool(binding.get("generated", profile.scope == "world_local")),
    )


def _raise_not_ready(status: str) -> NoReturn:
    raise ValueError(f"world_profile_not_ready:{status}")


def profile_manifest_run(world: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project profile state as a non-persisted authoring graph before lore starts."""

    metadata = dict(world.get("metadata") or {})
    binding = dict(metadata.get("genre_profile_binding") or {})
    if not binding:
        return None
    status = str(binding.get("status") or "unresolved")
    if status in {"ready", "approved"}:
        resolution = profile_resolution_from_world(world, allow_legacy_missing=False)
        assert resolution is not None
        graph: Any = build_profile_topic_graph(
            resolution.profile,
            campaign_template=str(
                metadata.get("campaign_template") or resolution.profile.profile_id
            ),
            depth="standard",
            tone=str(world.get("tone") or ""),
            background_expansion=True,
            runtime_capabilities=dict(metadata.get("runtime_capabilities") or {}),
        )
        run_status = "empty"
    else:
        graph = {
            "graph_version": "rpg_world_profile_pending_v1",
            "campaign_template": str(metadata.get("campaign_template") or ""),
            "depth": "standard",
            "nodes": [
                {
                    "topic_id": "profile_resolution",
                    "title": "Genre Profile",
                    "category": "bootstrap",
                    "dependencies": [],
                    "generator_role": "profile_architect",
                    "required_before_launch": True,
                    "visibility": "game_master_canon",
                    "target_count": 1,
                    "metadata": {"profile_status": status},
                }
            ],
            "launch_required_topic_ids": ["profile_resolution"],
            "metadata": {"profile_status": status},
        }
        run_status = "running" if status == "generating" else "failed"
    graph_payload = graph.as_dict() if hasattr(graph, "as_dict") else graph
    return {
        "run_id": f"profile-manifest:{world.get('id')}",
        "world_id": str(world.get("id") or ""),
        "draft_revision": int(world.get("draft_revision") or 1),
        "status": run_status,
        "graph": graph_payload,
        "context": {"genre_profile_binding": binding},
        "settings": {},
        "plan": {"job_ids": [binding.get("job_id")] if binding.get("job_id") else []},
        "progress": {
            "active_topic_ids": ["profile_resolution"] if status == "generating" else [],
            "failed_topic_ids": ["profile_resolution"] if status == "validation_failed" else [],
        },
        "error": dict(binding.get("error") or {}),
    }


def _current_profile_input(world: Mapping[str, Any]) -> dict[str, str]:
    metadata = dict(world.get("metadata") or {})
    return _profile_input(
        title=str(world.get("title") or ""),
        genre=str(world.get("genre") or ""),
        description=str(world.get("description") or ""),
        tone=str(world.get("tone") or ""),
        campaign_mode=str(metadata.get("campaign_mode") or "persistent_living_world"),
    )


def _set_binding(
    work: Any,
    context: Any,
    *,
    world: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> None:
    metadata = {
        **dict(world.get("metadata") or {}),
        "genre_profile_binding": dict(binding),
    }
    updated = work.connection.execute(
        "UPDATE omnix_rpg_worlds SET metadata_jsonb = %s::jsonb, "
        "updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s AND id = %s RETURNING id",
        (json.dumps(metadata, sort_keys=True), context.workspace_id, str(world["id"])),
    ).fetchone()
    if updated is None:
        raise RuntimeError("world_profile_binding_update_failed")


def execute_claimed_world_profile_job(
    *,
    job: Mapping[str, Any],
    worker_id: str,
    database: Any | None = None,
    generator: GenreProfileGenerator | None = None,
) -> dict[str, Any]:
    if str(job.get("job_type") or "") != WORLD_PROFILE_JOB_TYPE:
        raise ValueError("not_world_profile_job")
    payload = dict(job.get("input_payload") or {})
    world_id = str(payload.get("world_id") or "")
    lease_token = str(job.get("lease_token") or "")
    context = current_tenant()
    try:
        with unit_of_work(database) as work:
            world = work.world_scenarios.get_world(context, world_id)
            if world is None:
                raise KeyError(f"world_not_found:{world_id}")
            expected_hash = str(payload.get("profile_input_hash") or "")
            if canonical_hash(_current_profile_input(world)) != expected_hash:
                raise RuntimeError("world_profile_input_changed")
            work.rollback()

        profile_input = dict(payload.get("profile_input") or {})
        selected_generator = generator or build_genre_profile_generator_from_settings(
            dict(payload.get("settings") or {})
        )
        from app.apps.rpg.foundation.llm_priority import background_rpg_llm_priority

        with background_rpg_llm_priority():
            resolution = resolve_or_generate_genre_profile(
                genre=str(profile_input.get("genre") or ""),
                description=str(profile_input.get("description") or ""),
                campaign_mode=str(
                    profile_input.get("campaign_mode") or "persistent_living_world"
                ),
                registry=GenreProfileRegistry(),
                generator=selected_generator,
            )

        with unit_of_work(database) as work:
            world = work.world_scenarios.get_world(context, world_id)
            if world is None:
                raise KeyError(f"world_not_found:{world_id}")
            if canonical_hash(_current_profile_input(world)) != str(
                payload.get("profile_input_hash") or ""
            ):
                raise RuntimeError("world_profile_input_changed")
            route = dict(dict(world.get("metadata") or {}).get("genre_profile_binding") or {}).get(
                "route"
            )
            route = dict(route or {})
            binding = _ready_binding(
                resolution,
                input_hash=str(payload.get("profile_input_hash") or ""),
                route=ResolvedWorldForgeRoute(
                    provider=str(route.get("provider") or ""),
                    model=str(route.get("model") or ""),
                    source=str(route.get("source") or "durable_job"),
                    requested_provider=str(route.get("provider") or ""),
                    requested_model=str(route.get("model") or ""),
                ),
                job_id=str(job.get("id") or ""),
            )
            _set_binding(work, context, world=world, binding=binding)
            completed = work.jobs.complete(
                context,
                job_id=str(job["id"]),
                worker_id=worker_id,
                lease_token=lease_token,
                output_refs=[
                    {
                        "world_id": world_id,
                        "profile_id": resolution.profile.profile_id,
                        "profile_hash": resolution.profile.content_hash,
                    }
                ],
                progress={"current": 1, "total": 1, "message": "genre profile ready"},
            )
            work.commit()
        return {
            "ok": True,
            "status": "completed",
            "job": completed,
            "world_id": world_id,
            "profile_hash": resolution.profile.content_hash,
        }
    except DatabaseUnavailableError:
        raise
    except Exception as exc:
        with unit_of_work(database) as work:
            failed = work.jobs.fail(
                context,
                job_id=str(job["id"]),
                worker_id=worker_id,
                lease_token=lease_token,
                error={"code": "world_profile_generation_failed", "message": str(exc)},
                retry_delay_seconds=1,
            )
            world = work.world_scenarios.get_world(context, world_id)
            if world is not None and str(failed.get("status") or "") == "failed":
                metadata = dict(world.get("metadata") or {})
                binding = dict(metadata.get("genre_profile_binding") or {})
                binding.update(
                    {
                        "status": "validation_failed",
                        "error": {
                            "code": "world_profile_generation_failed",
                            "message": str(exc),
                        },
                    }
                )
                _set_binding(work, context, world=world, binding=binding)
            work.commit()
        return {
            "ok": False,
            "status": failed["status"],
            "job": failed,
            "world_id": world_id,
            "error": "world_profile_generation_failed",
            "detail": str(exc),
        }
