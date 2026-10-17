"""Lease-backed worker for progressive reusable-world map materialization."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from .progressive_materialization import materialize_deferred_location
from .progressive_materialization_job_planning import (
    MATERIALIZATION_RESOURCE_CLASS,
)

_DEFAULT_LEASE_SECONDS = 900
_worker_lock = threading.Lock()
_worker_active = False


def _database(value: Any | None) -> Any:
    if value is not None:
        return value
    from app.persistence.database import default_database

    return default_database()


def run_materialization_worker_once(
    *,
    worker_id: str = "rpg-map-materialization:local",
    database: Any | None = None,
    materializer: Callable[..., dict[str, Any]] = materialize_deferred_location,
    retry_delay_seconds: int | None = None,
) -> dict[str, Any] | None:
    db = _database(database)
    context = current_tenant()
    with unit_of_work(db) as work:
        job = work.jobs.claim_next(
            context,
            worker_id=worker_id,
            resource_classes=[MATERIALIZATION_RESOURCE_CLASS],
            lease_seconds=_DEFAULT_LEASE_SECONDS,
        )
        if job is None:
            work.rollback()
            return None
        job = work.jobs.mark_running(
            context,
            job_id=job["id"],
            worker_id=worker_id,
            lease_token=str(job["lease_token"]),
        )
        work.commit()
    payload = dict(job.get("input_payload") or {})
    try:
        result = materializer(
            world_id=str(payload["world_id"]),
            source_world_revision=int(payload["source_world_revision"]),
            location_id=str(payload["location_id"]),
            database=db,
        )
        materialization = dict(result.get("materialization") or {})
        with unit_of_work(db) as work:
            completed = work.jobs.complete(
                context,
                job_id=job["id"],
                worker_id=worker_id,
                lease_token=str(job["lease_token"]),
                output_refs=[
                    {
                        "kind": "rpg_world_release",
                        "world_id": payload["world_id"],
                        "world_revision": materialization.get("world_revision"),
                        "world_release": materialization.get("world_release"),
                        "location_id": payload["location_id"],
                    }
                ],
                progress={
                    "current": 1,
                    "total": 1,
                    "percent": 100,
                    "message": "materialized",
                    "reused": bool(result.get("reused")),
                },
            )
            work.commit()
        return {"ok": True, "job": completed, "result": result}
    except Exception as exc:
        attempt = int(job.get("attempt_count") or 1)
        delay = (
            max(0, int(retry_delay_seconds))
            if retry_delay_seconds is not None
            else min(300, 15 * (2 ** max(0, attempt - 1)))
        )
        with unit_of_work(db) as work:
            failed = work.jobs.fail(
                context,
                job_id=job["id"],
                worker_id=worker_id,
                lease_token=str(job["lease_token"]),
                error={
                    "code": "progressive_materialization_failed",
                    "message": str(exc),
                    "retryable": attempt < int(job.get("max_attempts") or 1),
                    "details": {
                        "exception_type": type(exc).__name__,
                        "world_id": payload.get("world_id"),
                        "source_world_revision": payload.get("source_world_revision"),
                        "location_id": payload.get("location_id"),
                    },
                },
                retry_delay_seconds=delay,
            )
            work.commit()
        return {"ok": False, "job": failed, "error": failed.get("error")}


def _worker_loop(database: Any | None) -> None:
    global _worker_active
    try:
        while run_materialization_worker_once(database=database) is not None:
            pass
    finally:
        with _worker_lock:
            _worker_active = False


def kick_materialization_worker(*, database: Any | None = None) -> bool:
    global _worker_active
    with _worker_lock:
        if _worker_active:
            return False
        _worker_active = True
    thread = threading.Thread(
        target=_worker_loop,
        args=(database,),
        daemon=True,
        name="omnix-rpg-map-materialization",
    )
    thread.start()
    return True
