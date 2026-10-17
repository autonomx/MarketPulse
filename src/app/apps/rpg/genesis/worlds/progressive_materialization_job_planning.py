"""Deterministic planning for progressive-map materialization jobs."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.jobs.models import ResourceClass

from .contracts import WorldReleaseDocument, WorldRevisionDocument
from .generation_jobs import canonical_hash
from .starter_bubble import StarterBubblePlan, predictive_materialization_queue

MATERIALIZATION_JOB_TYPE = "rpg.world.map.materialize"
MATERIALIZATION_JOB_CONTRACT = "rpg_progressive_materialization_job_v1"
MATERIALIZATION_RESOURCE_CLASS = ResourceClass.RPG_MAP_MATERIALIZATION.value


def materialization_job_id(
    *,
    workspace_id: str,
    world_id: str,
    source_world_revision: int,
    location_id: str,
) -> str:
    digest = canonical_hash(
        {
            "workspace_id": workspace_id,
            "world_id": world_id,
            "source_world_revision": int(source_world_revision),
            "location_id": location_id,
            "contract_version": MATERIALIZATION_JOB_CONTRACT,
        }
    ).removeprefix("sha256:")[:28]
    return f"rpg-map-materialization:{digest}"


def load_starter_plan(
    work: Any,
    context: Any,
    *,
    world_id: str,
    source_world_revision: int,
) -> StarterBubblePlan:
    revision_row = work.world_scenarios.get_world_revision(
        context,
        world_id,
        source_world_revision,
    )
    if revision_row is None:
        raise KeyError(f"world_revision_not_found:{world_id}:{source_world_revision}")
    release_row = work.connection.execute(
        "SELECT document_jsonb FROM omnix_rpg_world_releases "
        "WHERE workspace_id = %s AND world_id = %s AND world_revision = %s "
        "ORDER BY release DESC LIMIT 1",
        (context.workspace_id, world_id, int(source_world_revision)),
    ).fetchone()
    if release_row is None:
        raise KeyError(f"world_release_not_found:{world_id}:{source_world_revision}")
    revision = WorldRevisionDocument.model_validate(revision_row["document"])
    release = WorldReleaseDocument.model_validate(release_row[0])
    payload = release.indexes.get("starter_bubble")
    if not isinstance(payload, Mapping):
        payload = revision.topology.get("starter_bubble")
    if not isinstance(payload, Mapping):
        raise ValueError("starter_bubble_plan_missing")
    return StarterBubblePlan.model_validate(payload)


def predictive_candidates(
    plan: StarterBubblePlan,
    *,
    current_location_id: str,
    route_intent_location_id: str | None,
    minimum_score: float,
) -> tuple[dict[str, Any], ...]:
    try:
        plan.slot(current_location_id)
    except KeyError as exc:
        raise ValueError(f"current_location_not_in_starter_plan:{current_location_id}") from exc
    rows = {
        str(row["location_id"]): {
            **dict(row),
            "trigger_reasons": ["campaign_proximity"],
        }
        for row in predictive_materialization_queue(
            plan,
            current_location_id=current_location_id,
            minimum_score=max(0.0, min(float(minimum_score), 1.0)),
        )
    }
    if route_intent_location_id:
        try:
            intended = plan.slot(route_intent_location_id)
        except KeyError as exc:
            raise ValueError(
                f"route_intent_location_not_in_starter_plan:{route_intent_location_id}"
            ) from exc
        if intended.deferred and intended.map_id:
            row = rows.setdefault(
                intended.location_id,
                {
                    "location_id": intended.location_id,
                    "map_id": intended.map_id,
                    "priority": round(intended.predictive_score, 3),
                    "presentation_optional": True,
                    "fallback": "navigable_placeholder",
                    "trigger_reasons": [],
                },
            )
            reasons = list(row.get("trigger_reasons") or ())
            if "route_intent" not in reasons:
                reasons.append("route_intent")
            row["trigger_reasons"] = reasons
            row["priority"] = max(float(row.get("priority") or 0.0), 0.95)
    return tuple(
        rows[location_id]
        for location_id in sorted(
            rows,
            key=lambda value: (-float(rows[value].get("priority") or 0.0), value),
        )
    )


def job_payload(
    *,
    workspace_id: str,
    world_id: str,
    source_world_revision: int,
    candidate: Mapping[str, Any],
    current_location_id: str,
    route_intent_location_id: str | None,
    campaign_id: str | None,
    max_attempts: int,
) -> dict[str, Any]:
    location_id = str(candidate["location_id"])
    input_payload = {
        "contract_version": MATERIALIZATION_JOB_CONTRACT,
        "world_id": world_id,
        "source_world_revision": int(source_world_revision),
        "location_id": location_id,
        "map_id": candidate.get("map_id"),
        "campaign_id": campaign_id,
        "current_location_id": current_location_id,
        "route_intent_location_id": route_intent_location_id,
        "trigger_reasons": list(candidate.get("trigger_reasons") or ()),
        "presentation_optional": bool(candidate.get("presentation_optional", True)),
        "fallback": str(candidate.get("fallback") or "navigable_placeholder"),
    }
    return {
        "id": materialization_job_id(
            workspace_id=workspace_id,
            world_id=world_id,
            source_world_revision=source_world_revision,
            location_id=location_id,
        ),
        "module": "rpg",
        "job_type": MATERIALIZATION_JOB_TYPE,
        "resource_class": MATERIALIZATION_RESOURCE_CLASS,
        "priority": int(round(float(candidate.get("priority") or 0.0) * 100)),
        "max_attempts": max(1, int(max_attempts)),
        "input_payload": input_payload,
        "metadata": {
            "contract_version": MATERIALIZATION_JOB_CONTRACT,
            "world_id": world_id,
            "source_world_revision": int(source_world_revision),
            "location_id": location_id,
            "campaign_id": campaign_id,
            "trigger_reasons": list(candidate.get("trigger_reasons") or ()),
        },
    }
