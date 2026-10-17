"""Scheduling and telemetry for durable progressive-map materialization jobs."""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from .progressive_materialization_job_planning import (
    MATERIALIZATION_JOB_TYPE,
    job_payload,
    load_starter_plan,
    predictive_candidates,
)


def _database(value: Any | None) -> Any:
    if value is not None:
        return value
    from app.persistence.database import default_database

    return default_database()


def schedule_predictive_materialization(
    *,
    world_id: str,
    source_world_revision: int,
    current_location_id: str,
    route_intent_location_id: str | None = None,
    campaign_id: str | None = None,
    minimum_score: float = 0.35,
    max_attempts: int = 3,
    database: Any | None = None,
    kick_worker: bool = True,
    allow_missing_plan: bool = False,
) -> dict[str, Any]:
    db = _database(database)
    context = current_tenant()
    try:
        with unit_of_work(db) as work:
            plan = load_starter_plan(
                work,
                context,
                world_id=world_id,
                source_world_revision=source_world_revision,
            )
            candidates = predictive_candidates(
                plan,
                current_location_id=current_location_id,
                route_intent_location_id=route_intent_location_id,
                minimum_score=minimum_score,
            )
            scheduled: list[dict[str, Any]] = []
            for candidate in candidates:
                payload = job_payload(
                    workspace_id=context.workspace_id,
                    world_id=world_id,
                    source_world_revision=source_world_revision,
                    candidate=candidate,
                    current_location_id=current_location_id,
                    route_intent_location_id=route_intent_location_id,
                    campaign_id=campaign_id,
                    max_attempts=max_attempts,
                )
                job, created = work.jobs.create_job_once(context, payload)
                scheduled.append(
                    {
                        "job_id": job["id"],
                        "status": job["status"],
                        "created": created,
                        "location_id": candidate["location_id"],
                        "priority": job["priority"],
                        "trigger_reasons": list(
                            candidate.get("trigger_reasons") or ()
                        ),
                    }
                )
            work.commit()
    except ValueError as exc:
        if allow_missing_plan and str(exc) == "starter_bubble_plan_missing":
            return {
                "ok": True,
                "status": "not_applicable",
                "world_id": world_id,
                "source_world_revision": int(source_world_revision),
                "scheduled": [],
                "worker_started": False,
                "reason": "starter_bubble_plan_missing",
            }
        raise
    worker_started = False
    if scheduled and kick_worker:
        from .progressive_materialization_worker import kick_materialization_worker

        worker_started = kick_materialization_worker(database=db)
    return {
        "ok": True,
        "status": "scheduled" if scheduled else "idle",
        "world_id": world_id,
        "source_world_revision": int(source_world_revision),
        "campaign_id": campaign_id,
        "current_location_id": current_location_id,
        "route_intent_location_id": route_intent_location_id,
        "scheduled": scheduled,
        "worker_started": worker_started,
    }


def schedule_campaign_predictive_materialization(
    campaign_id: str,
    *,
    current_location_id: str,
    route_intent_location_id: str | None = None,
    minimum_score: float = 0.35,
    database: Any | None = None,
    kick_worker: bool = True,
    allow_missing_plan: bool = False,
) -> dict[str, Any]:
    db = _database(database)
    context = current_tenant()
    with unit_of_work(db) as work:
        binding = work.world_scenarios.get_campaign_binding(context, campaign_id)
        work.rollback()
    if binding is None:
        raise KeyError(f"campaign_world_binding_not_found:{campaign_id}")
    return schedule_predictive_materialization(
        world_id=str(binding["world_id"]),
        source_world_revision=int(binding["world_revision"]),
        current_location_id=current_location_id,
        route_intent_location_id=route_intent_location_id,
        campaign_id=campaign_id,
        minimum_score=minimum_score,
        database=db,
        kick_worker=kick_worker,
        allow_missing_plan=allow_missing_plan,
    )


def materialization_job_telemetry(
    *,
    world_id: str,
    source_world_revision: int | None = None,
    database: Any | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    db = _database(database)
    context = current_tenant()
    with unit_of_work(db) as work:
        rows = list(work.jobs.iter_jobs(context, job_types=(MATERIALIZATION_JOB_TYPE,)))
        work.rollback()
    jobs: list[Any] = []
    for row in rows:
        if len(jobs) >= max(1, int(limit)):
            break
        payload = dict(row.get("input_payload") or {})
        if str(payload.get("world_id") or "") != world_id:
            continue
        if source_world_revision is not None and int(
            payload.get("source_world_revision") or 0
        ) != int(source_world_revision):
            continue
        jobs.append(
            {
                "job_id": row["id"],
                "status": row["status"],
                "source_world_revision": int(
                    payload.get("source_world_revision") or 0
                ),
                "location_id": str(payload.get("location_id") or ""),
                "campaign_id": payload.get("campaign_id"),
                "trigger_reasons": list(payload.get("trigger_reasons") or ()),
                "priority": row["priority"],
                "attempt_count": row["attempt_count"],
                "max_attempts": row["max_attempts"],
                "progress": dict(row.get("progress") or {}),
                "error": dict(row["error"]) if row.get("error") else None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "completed_at": row.get("completed_at"),
            }
        )
    counts = Counter(str(row["status"]) for row in jobs)
    active_statuses = {"queued", "waiting", "leased", "running", "retrying"}
    pending = sum(counts.get(status, 0) for status in active_statuses)
    return {
        "ok": True,
        "world_id": world_id,
        "source_world_revision": source_world_revision,
        "status": "active" if pending else ("failed" if counts.get("failed") else "idle"),
        "counts": dict(sorted(counts.items())),
        "pending": pending,
        "attempts": sum(int(row["attempt_count"]) for row in jobs),
        "failed_location_ids": sorted(
            row["location_id"] for row in jobs if row["status"] == "failed"
        ),
        "completed_location_ids": sorted(
            row["location_id"] for row in jobs if row["status"] == "completed"
        ),
        "jobs": jobs,
    }
