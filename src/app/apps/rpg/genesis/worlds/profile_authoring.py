"""Editable world-local genre profile review and approval services."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Mapping

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.forge.world_forge_profile_generation import (
    default_profile_registry,
    resolve_or_generate_genre_profile,
)
from app.apps.rpg.genesis.forge.world_forge_profiles import genre_profile_from_dict

from .lifecycle_service import require_world_writable


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _integer(value: Any, fallback: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return fallback


def profile_review_from_world(world: Mapping[str, Any]) -> dict[str, Any]:
    """Project the durable binding into a stable author-review contract."""

    metadata = _record(world.get("metadata"))
    binding = _record(metadata.get("genre_profile_binding"))
    profile_payload = _record(binding.get("profile"))
    profile_hash = str(binding.get("profile_hash") or "")
    approved_hash = str(binding.get("approved_profile_hash") or "")
    raw_status = str(binding.get("status") or "unresolved")
    revision = _integer(binding.get("profile_revision"), 1)

    if raw_status == "ready":
        status = "approved" if approved_hash and approved_hash == profile_hash else "review_required"
    elif raw_status == "approved" and approved_hash != profile_hash:
        status = "review_required"
    else:
        status = raw_status

    return {
        "world_id": str(world.get("id") or ""),
        "status": status,
        "profile_revision": revision,
        "profile_hash": profile_hash,
        "approved_profile_hash": approved_hash,
        "approved_at": binding.get("approved_at"),
        "approved_by": binding.get("approved_by"),
        "profile": profile_payload,
        "requested_genre": str(binding.get("requested_genre") or world.get("genre") or ""),
        "normalized_genre": str(binding.get("normalized_genre") or ""),
        "source": str(binding.get("source") or ""),
        "generated": bool(binding.get("generated", False)),
        "route": _record(binding.get("route")),
        "review_findings": list(binding.get("review_findings") or ()),
        "error": _record(binding.get("error")),
    }


def _store_binding(
    work: Any,
    context: Any,
    *,
    world: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> None:
    metadata = {
        **_record(world.get("metadata")),
        "genre_profile_binding": dict(binding),
    }
    updated = work.connection.execute(
        "UPDATE omnix_rpg_worlds SET metadata_jsonb = %s::jsonb, "
        "updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s AND id = %s RETURNING id",
        (json.dumps(metadata, sort_keys=True), context.workspace_id, str(world["id"])),
    ).fetchone()
    if updated is None:
        raise RuntimeError("world_profile_binding_update_failed")


def _replacement_binding(
    world: Mapping[str, Any],
    existing: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Create a review draft for legacy or superseded registry profiles."""

    # A durable profile job owns these states. Replacing its empty profile payload
    # with a heuristic migration while it is still running would discard the
    # provider result and make the UI claim review is ready too early.
    existing_status = str(existing.get("status") or "")
    if existing_status in {"generating", "validation_failed"}:
        return None

    genre = str(world.get("genre") or "classic_fantasy")
    registry_profile = default_profile_registry().resolve(genre)
    current_profile = _record(existing.get("profile"))
    current_source = str(existing.get("source") or "")
    should_replace = not current_profile
    if registry_profile is not None and current_source in {"", "registry"}:
        should_replace = (
            should_replace
            or str(existing.get("profile_hash") or "")
            != registry_profile.content_hash
        )
    if not should_replace:
        return None

    metadata = _record(world.get("metadata"))
    if registry_profile is not None:
        profile = registry_profile
        source = "registry"
        normalized_genre = profile.profile_id
        generated = False
    else:
        resolution = resolve_or_generate_genre_profile(
            genre=genre,
            description=str(world.get("description") or ""),
            campaign_mode=str(
                metadata.get("campaign_mode") or "persistent_living_world"
            ),
        )
        profile = resolution.profile
        source = resolution.source
        normalized_genre = resolution.normalized_genre
        generated = resolution.generated

    return {
        **dict(existing),
        "status": "ready",
        "requested_genre": genre,
        "normalized_genre": normalized_genre,
        "source": source,
        "generated": generated,
        "profile_id": profile.profile_id,
        "profile_version": profile.version,
        "profile_hash": profile.content_hash,
        "profile": profile.as_dict(),
        "profile_revision": _integer(existing.get("profile_revision"), 0)
        + (1 if existing else 0),
        "approved_profile_hash": "",
        "approved_at": None,
        "approved_by": None,
        "route": _record(existing.get("route"))
        or {
            "provider": "registry" if registry_profile is not None else "deterministic",
            "model": "",
            "source": "profile_review_migration",
        },
        "review_findings": [
            {
                "code": "profile_catalogue_upgraded",
                "message": "The world profile was upgraded to the current standard topic catalogue and requires approval.",
            }
        ],
        "error": {},
    }


def read_world_profile_review(
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
        existing = _record(
            _record(world.get("metadata")).get("genre_profile_binding")
        )
        replacement = _replacement_binding(world, existing)
        if replacement is not None:
            _store_binding(work, context, world=world, binding=replacement)
            work.commit()
            world = {
                **dict(world),
                "metadata": {
                    **_record(world.get("metadata")),
                    "genre_profile_binding": replacement,
                },
            }
        else:
            work.rollback()
    return {"ok": True, "review": profile_review_from_world(world)}


def update_world_profile_review(
    world_id: str,
    *,
    expected_profile_revision: int,
    profile: Mapping[str, Any],
    database: Any | None = None,
) -> dict[str, Any]:
    """Validate and save an edited profile, invalidating prior approval."""

    validated = genre_profile_from_dict(profile).require_valid()
    context = current_tenant()
    with unit_of_work(database) as work:
        world = require_world_writable(work, context, world_id)
        current = profile_review_from_world(world)
        if int(current["profile_revision"]) != int(expected_profile_revision):
            raise ValueError(
                "world_profile_revision_conflict:"
                f"expected={expected_profile_revision}:current={current['profile_revision']}"
            )
        existing = _record(
            _record(world.get("metadata")).get("genre_profile_binding")
        )
        next_revision = int(current["profile_revision"]) + 1
        binding = {
            **existing,
            "status": "review_required",
            "profile": validated.as_dict(),
            "profile_id": validated.profile_id,
            "profile_version": validated.version,
            "profile_hash": validated.content_hash,
            "profile_revision": next_revision,
            "approved_profile_hash": "",
            "approved_at": None,
            "approved_by": None,
            "review_findings": [],
        }
        _store_binding(work, context, world=world, binding=binding)
        stale_rows = work.connection.execute(
            "UPDATE omnix_rpg_world_topics SET status = 'stale', updated_at = CURRENT_TIMESTAMP "
            "WHERE workspace_id = %s AND world_id = %s AND status = 'ready' RETURNING topic_id",
            (context.workspace_id, world_id),
        ).fetchall()
        work.commit()
    review = profile_review_from_world(
        {
            **dict(world),
            "metadata": {
                **_record(world.get("metadata")),
                "genre_profile_binding": binding,
            },
        }
    )
    return {
        "ok": True,
        "review": review,
        "stale_topic_ids": [str(row[0]) for row in stale_rows],
    }


def approve_world_profile_review(
    world_id: str,
    *,
    expected_profile_revision: int,
    approved_by: str = "local-author",
    database: Any | None = None,
) -> dict[str, Any]:
    """Approve exactly one validated profile revision and hash."""

    context = current_tenant()
    with unit_of_work(database) as work:
        world = require_world_writable(work, context, world_id)
        current = profile_review_from_world(world)
        if int(current["profile_revision"]) != int(expected_profile_revision):
            raise ValueError(
                "world_profile_revision_conflict:"
                f"expected={expected_profile_revision}:current={current['profile_revision']}"
            )
        if not current["profile"]:
            raise ValueError("world_profile_not_ready")
        validated = genre_profile_from_dict(_record(current["profile"])).require_valid()
        if validated.content_hash != str(current["profile_hash"]):
            raise ValueError("world_profile_hash_mismatch")
        existing = _record(
            _record(world.get("metadata")).get("genre_profile_binding")
        )
        binding = {
            **existing,
            "status": "ready",
            "profile": validated.as_dict(),
            "profile_id": validated.profile_id,
            "profile_version": validated.version,
            "profile_hash": validated.content_hash,
            "profile_revision": int(current["profile_revision"]),
            "approved_profile_hash": validated.content_hash,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "approved_by": str(approved_by or "local-author"),
            "review_findings": [],
        }
        _store_binding(work, context, world=world, binding=binding)
        work.commit()
    review = profile_review_from_world(
        {
            **dict(world),
            "metadata": {
                **_record(world.get("metadata")),
                "genre_profile_binding": binding,
            },
        }
    )
    return {"ok": True, "review": review}


def require_approved_profile(world: Mapping[str, Any]) -> dict[str, Any]:
    """Reject lore generation unless the current profile hash is approved."""

    review = profile_review_from_world(world)
    if (
        review["status"] != "approved"
        or not review["profile_hash"]
        or review["approved_profile_hash"] != review["profile_hash"]
    ):
        raise ValueError("world_profile_approval_required")
    genre_profile_from_dict(_record(review["profile"])).require_valid()
    return review
