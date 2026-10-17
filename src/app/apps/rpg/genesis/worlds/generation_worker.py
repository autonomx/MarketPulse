"""Lease-backed worker for reusable-world generation jobs."""
from __future__ import annotations
from app.config.env import environment as _environment

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, cast

from app.persistence.database import DatabaseUnavailableError
from app.apps.rpg.genesis.forge.world_forge_generation import WorldForgeTopicGenerator

from .generation_candidate_spool import (
    RawCandidateSpoolingWorldForgeGenerator,
    ReplayedRawCandidateWorldForgeGenerator,
    delete_provider_started_spool,
    delete_raw_candidate_spool,
    read_candidate_spool,
    read_provider_started_spool,
    read_raw_candidate_spool,
)
from .generation_coordinator import execute_claimed_world_topic_job
from .generation_diagnostics import log_world_generation_event
from .generation_failure_spool import (
    FailureSpoolingWorldForgeGenerator,
    ReplayedFailureWorldForgeGenerator,
    delete_failure_spool,
    read_failure_spool,
)
from .generation_jobs import WORLD_TOPIC_JOB_TYPE, WORLD_TOPIC_RESOURCE_CLASS
from .generation_validation import PublicationValidatedWorldForgeGenerator
from .profile_generation_jobs import (
    WORLD_PROFILE_JOB_TYPE,
    execute_claimed_world_profile_job,
)

_DEFAULT_LEASE_SECONDS = 3600
_DEFAULT_MAX_WORLD_GENERATION_WORKERS = 2
_LMSTUDIO_MAX_WORLD_GENERATION_WORKERS = 2
_RETRY_POLL_SECONDS = 1.1
_DATABASE_RECOVERY_POLL_SECONDS = 5.0
_DAG_IDLE_POLL_SECONDS = 0.25
_DAG_IDLE_POLLS_BEFORE_STOP = 4
_LOGGER = logging.getLogger(__name__)
_worker_lock = threading.Lock()
_worker_active = False
_SUPPORTED_JOB_TYPES = (WORLD_TOPIC_JOB_TYPE, WORLD_PROFILE_JOB_TYPE)


@dataclass
class _WorkerPoolState:
    condition: threading.Condition = field(default_factory=threading.Condition)
    calls_in_progress: int = 0
    completion_generation: int = 0
    stop: bool = False


def _database(value: Any | None) -> Any:
    if value is not None:
        return value
    from app.persistence.database import default_database

    return default_database()


def _provider_key(value: Any) -> str:
    provider = str(value or "").strip().casefold()
    if provider.startswith("llm:"):
        provider = provider.split(":", 1)[1]
    return provider


def _configured_world_forge_provider(
    environ: Mapping[str, str] | None = None,
) -> str:
    env = environ or _environment()
    dedicated = _provider_key(env.get("OMNIX_RPG_WORLD_FORGE_PROVIDER"))
    if dedicated:
        return dedicated
    try:
        from app.settings.effective_defaults import (
            effective_llm_route,
            load_effective_profile,
        )

        provider_id, _model_id = effective_llm_route(
            load_effective_profile(),
            "rpg",
            "rpg.world_forge.generate",
        )
        return _provider_key(provider_id)
    except Exception:
        return ""


def world_generation_worker_limit(
    environ: Mapping[str, str] | None = None,
    *,
    provider_route: str = "",
) -> int:
    """Return a safe worker count for the provider that owns this durable run."""

    env = environ or _environment()
    override = str(env.get("OMNIX_RPG_WORLD_GENERATION_WORKERS") or "").strip()
    if override:
        try:
            return max(1, min(int(override), _DEFAULT_MAX_WORLD_GENERATION_WORKERS))
        except ValueError:
            _LOGGER.warning(
                "Ignoring invalid OMNIX_RPG_WORLD_GENERATION_WORKERS=%r",
                override,
            )
    provider = _provider_key(provider_route) or _configured_world_forge_provider(env)
    if provider == "lmstudio":
        return _LMSTUDIO_MAX_WORLD_GENERATION_WORKERS
    return _DEFAULT_MAX_WORLD_GENERATION_WORKERS


def _job_fields(job: Mapping[str, Any], *, worker_id: str) -> dict[str, Any]:
    payload = dict(job.get("input_payload") or {})
    settings = dict(payload.get("settings") or {})
    metadata = dict(job.get("metadata") or {})
    return {
        "worker_id": worker_id,
        "attempt_count": job.get("attempt_count"),
        "max_attempts": job.get("max_attempts"),
        "content_generation_attempts": settings.get("content_generation_attempts", 1),
        "status": job.get("status"),
        "job_type": job.get("job_type"),
        "provider_route": settings.get("provider_route"),
        "model": settings.get("model"),
        "generator_version": settings.get("generator_version"),
        "prompt_version": settings.get("prompt_version"),
        "dependency_ids": metadata.get("dependency_ids") or [],
        "dependency_trust": metadata.get("dependency_trust") or {},
    }


def _topic_resume_phase(job_id: str) -> str:
    if (
        read_candidate_spool(job_id) is not None
        or read_raw_candidate_spool(job_id) is not None
        or read_failure_spool(job_id) is not None
    ):
        return "spool_ready"
    if read_provider_started_spool(job_id) is not None:
        return "provider_started_without_spool"
    return "provider_not_started"


def _release_interrupted_job(
    work: Any,
    context: Any,
    *,
    job_id: str,
    job_type: str,
    attempt_count: int,
    max_attempts: int,
    error_code: str,
) -> bool:
    if job_type == WORLD_TOPIC_JOB_TYPE:
        phase = _topic_resume_phase(job_id)
        retryable = phase in {"spool_ready", "provider_not_started"}
        next_max = max(max_attempts, attempt_count + 1) if retryable else max_attempts
        resume_policy = (
            "persist_existing_spool"
            if phase == "spool_ready"
            else "provider_not_started"
            if phase == "provider_not_started"
            else "provider_started_spool_missing"
        )
    else:
        retryable = attempt_count < max_attempts
        next_max = max_attempts
        resume_policy = "profile_retry_budget"
    return work.jobs.release_interrupted_job(
        context,
        job_id=job_id,
        status="retrying" if retryable else "failed",
        max_attempts=next_max,
        error_code=error_code,
        resume_policy=resume_policy,
    )


def _recover_interrupted_jobs(*, database: Any | None = None) -> dict[str, int]:
    """Discard orphans and recover jobs from durable provider/spool phase evidence."""

    db = _database(database)
    from app.security.tenant_context import current_tenant
    from app.persistence.unit_of_work import unit_of_work

    context = current_tenant()
    with unit_of_work(db) as work:
        discarded = 0
        cursor = None
        while True:
            batch = work.jobs.query_jobs(
                context,
                job_types=(WORLD_TOPIC_JOB_TYPE, WORLD_PROFILE_JOB_TYPE),
                order_by="created_asc",
                after_created=cursor,
                limit=500,
            )
            if not batch:
                break
            orphan_ids = []
            for job in batch:
                metadata = job.get("metadata") or {}
                world_id = str(metadata.get("world_id") or "")
                run_id = str(metadata.get("run_id") or "")
                world = work.world_scenarios.get_world(context, world_id) if world_id else None
                run = (
                    work.world_generation.get(context, run_id)
                    if run_id and job["job_type"] == WORLD_TOPIC_JOB_TYPE
                    else None
                )
                if world is None or (job["job_type"] == WORLD_TOPIC_JOB_TYPE and run is None):
                    orphan_ids.append(job["id"])
            discarded += work.jobs.delete_job_ids(context, job_ids=tuple(orphan_ids))
            last = batch[-1]
            cursor = (last["created_at"], last["id"])
        rows = []
        cursor = None
        while True:
            batch = work.jobs.query_jobs(
                context,
                job_types=(WORLD_TOPIC_JOB_TYPE, WORLD_PROFILE_JOB_TYPE),
                statuses=("leased", "running", "cancel_requested"),
                lease_owner_pattern="rpg-world-generation:local:%",
                order_by="created_asc",
                after_created=cursor,
                limit=500,
            )
            if not batch:
                break
            rows.extend(batch)
            last = batch[-1]
            cursor = (last["created_at"], last["id"])
        requeued = 0
        for row in rows:
            if _release_interrupted_job(
                work,
                context,
                job_id=str(cast(Any, row)[0]),
                job_type=str(row["job_type"]),
                attempt_count=int(row["attempt_count"]),
                max_attempts=int(row["max_attempts"]),
                error_code="worker_interrupted",
            ):
                requeued += 1
        work.commit()
    return {"discarded": int(discarded), "requeued": requeued}


def _recover_worker_database_interruption(
    worker_id: str,
    *,
    database: Any | None = None,
) -> int:
    """Release leases and extend persistence replay without extending content calls."""

    db = _database(database)
    from app.security.tenant_context import current_tenant
    from app.persistence.unit_of_work import unit_of_work

    context = current_tenant()
    with unit_of_work(db) as work:
        rows = work.jobs.query_jobs(
            context,
            job_types=(WORLD_TOPIC_JOB_TYPE, WORLD_PROFILE_JOB_TYPE),
            statuses=("leased", "running", "cancel_requested"),
            lease_owner=worker_id,
            limit=500,
        )
        recovered = 0
        for row in rows:
            if _release_interrupted_job(
                work,
                context,
                job_id=str(cast(Any, row)[0]),
                job_type=str(row["job_type"]),
                attempt_count=int(row["attempt_count"]),
                max_attempts=int(row["max_attempts"]),
                error_code="database_unavailable",
            ):
                recovered += 1
        work.commit()
    return recovered


def _missing_spool_failure(job: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(job.get("input_payload") or {})
    topic = dict(payload.get("topic") or {})
    topic_id = str(topic.get("topic_id") or "")
    return {
        "schema_version": "rpg_world_generation_failure_spool_v1",
        "run_id": str(payload.get("run_id") or ""),
        "world_id": str(payload.get("world_id") or ""),
        "draft_revision": int(payload.get("draft_revision") or 1),
        "topic_id": topic_id,
        "provider": {},
        "dependency_hashes": dict(payload.get("dependency_hashes") or {}),
        "dependency_trust": dict(payload.get("dependency_trust") or {}),
        "error_type": "GenerationSpoolMissing",
        "error_message": f"world_generation_spool_missing_after_provider_started:{topic_id}",
    }


def _topic_generator_for_job(
    job: Mapping[str, Any],
    supplied: WorldForgeTopicGenerator | None,
) -> WorldForgeTopicGenerator:
    """Replay durable evidence or construct the provider only before it is started."""

    job_id = str(job.get("id") or "")
    payload = dict(job.get("input_payload") or {})
    topic = dict(payload.get("topic") or {})
    failure = read_failure_spool(job_id)
    if failure is not None:
        return ReplayedFailureWorldForgeGenerator(failure)
    raw = read_raw_candidate_spool(job_id)
    if raw is not None:
        return ReplayedRawCandidateWorldForgeGenerator(raw)
    final = read_candidate_spool(job_id)
    if final is not None:
        return ReplayedRawCandidateWorldForgeGenerator(final)
    if read_provider_started_spool(job_id) is not None:
        return ReplayedFailureWorldForgeGenerator(_missing_spool_failure(job))

    selected = supplied
    if selected is None:
        from .generation_routing import build_world_forge_generator_from_settings

        selected = build_world_forge_generator_from_settings(
            dict(payload.get("settings") or {})
        )
    provider_guard = FailureSpoolingWorldForgeGenerator(
        selected,
        job_id=job_id,
        run_id=str(payload.get("run_id") or ""),
        world_id=str(payload.get("world_id") or ""),
        draft_revision=int(payload.get("draft_revision") or 1),
        topic_id=str(topic.get("topic_id") or ""),
        dependency_hashes=dict(payload.get("dependency_hashes") or {}),
        dependency_trust=dict(payload.get("dependency_trust") or {}),
    )
    return RawCandidateSpoolingWorldForgeGenerator(
        provider_guard,
        job_id=job_id,
        run_id=str(payload.get("run_id") or ""),
        world_id=str(payload.get("world_id") or ""),
        draft_revision=int(payload.get("draft_revision") or 1),
        topic_id=str(topic.get("topic_id") or ""),
        dependency_hashes=dict(payload.get("dependency_hashes") or {}),
        dependency_trust=dict(payload.get("dependency_trust") or {}),
    )


def _delete_terminal_phase_spools(job_id: str) -> None:
    delete_failure_spool(job_id)
    delete_raw_candidate_spool(job_id)
    delete_provider_started_spool(job_id)


def run_world_generation_worker_once(
    *,
    worker_id: str = "rpg-world-generation:local",
    database: Any | None = None,
    generator: WorldForgeTopicGenerator | None = None,
    profile_generator: Any | None = None,
) -> dict[str, Any] | None:
    """Claim and execute one world profile or topic job."""

    db = _database(database)
    from app.security.tenant_context import current_tenant
    from app.persistence.unit_of_work import unit_of_work

    context = current_tenant()
    with unit_of_work(db) as work:
        job = work.jobs.claim_next(
            context,
            worker_id=worker_id,
            resource_classes=[WORLD_TOPIC_RESOURCE_CLASS],
            lease_seconds=_DEFAULT_LEASE_SECONDS,
        )
        if job is None:
            work.rollback()
            return None
        job_type = str(job.get("job_type") or "")
        metadata = dict(job.get("metadata") or {})
        run_id = str(metadata.get("run_id") or "")
        world_id = str(metadata.get("world_id") or "")
        run = (
            work.world_generation.get(context, run_id)
            if run_id and job_type == WORLD_TOPIC_JOB_TYPE
            else None
        )
        world = work.world_scenarios.get_world(context, world_id) if world_id else None
        if job_type not in _SUPPORTED_JOB_TYPES:
            discard_reason = "unsupported_world_generation_job_type"
        elif not world_id:
            discard_reason = "missing_world_id"
        elif world is None:
            discard_reason = "world_not_found"
        elif job_type == WORLD_TOPIC_JOB_TYPE and not run_id:
            discard_reason = "missing_run_id"
        elif job_type == WORLD_TOPIC_JOB_TYPE and run is None:
            discard_reason = "world_generation_run_not_found"
        elif job_type == WORLD_TOPIC_JOB_TYPE and str(cast(Any, run)["world_id"]) != world_id:
            discard_reason = "world_generation_run_world_mismatch"
        else:
            discard_reason = ""
        if discard_reason:
            work.jobs.delete_claimed_job(
                context,
                job_id=str(job["id"]),
                lease_owner=worker_id,
                lease_token=str(job["lease_token"]),
            )
            work.commit()
            log_world_generation_event(
                "world_generation.orphaned_job_discarded",
                level="warning",
                world_id=world_id,
                run_id=run_id,
                topic_id=str(metadata.get("topic_id") or ""),
                job_id=str(job.get("id") or ""),
                fields={"reason": discard_reason, "worker_id": worker_id, "job_type": job_type},
            )
            return {"ok": True, "status": "discarded", "job": job, "detail": discard_reason}
        job = work.jobs.mark_running(
            context,
            job_id=job["id"],
            worker_id=worker_id,
            lease_token=str(job["lease_token"]),
        )
        work.commit()

    metadata = dict(job.get("metadata") or {})
    run_id = str(metadata.get("run_id") or "")
    world_id = str(metadata.get("world_id") or "")
    topic_id = str(metadata.get("topic_id") or "")
    job_type = str(job.get("job_type") or "")
    job_id = str(job.get("id") or "")
    log_world_generation_event(
        "world_generation.job_started",
        world_id=world_id,
        run_id=run_id,
        topic_id=topic_id,
        job_id=job_id,
        fields=_job_fields(job, worker_id=worker_id),
    )
    if job_type == WORLD_PROFILE_JOB_TYPE:
        result = execute_claimed_world_profile_job(
            job=job,
            worker_id=worker_id,
            database=db,
            generator=profile_generator,
        )
    else:
        selected_generator = PublicationValidatedWorldForgeGenerator(
            _topic_generator_for_job(job, generator)
        )
        result = execute_claimed_world_topic_job(
            job=job,
            worker_id=worker_id,
            generator=selected_generator,
            database=db,
        )
        if str(result.get("status") or "") in {"completed", "failed"}:
            _delete_terminal_phase_spools(job_id)
    status = str(result.get("status") or "unknown")
    ok = bool(result.get("ok"))
    fields = {
        **_job_fields(dict(result.get("job") or job), worker_id=worker_id),
        "result_status": result.get("result_status") or status,
        "detail": result.get("detail"),
    }
    log_world_generation_event(
        "world_generation.job_completed" if ok else "world_generation.job_attempt_failed",
        level="info" if ok else "error" if status == "failed" else "warning",
        world_id=world_id,
        run_id=run_id,
        topic_id=topic_id,
        job_id=job_id,
        fields=fields,
        error=result.get("detail") if not ok else None,
    )
    return result


def _worker_loop(slot: int, database: Any | None, state: _WorkerPoolState) -> None:
    worker_id = f"rpg-world-generation:local:{slot}"
    database_recovery_pending = False
    consecutive_empty_polls = 0
    while True:
        with state.condition:
            if state.stop:
                return
            state.calls_in_progress += 1
        try:
            if database_recovery_pending:
                requeued = _recover_worker_database_interruption(worker_id, database=database)
                database_recovery_pending = False
                log_world_generation_event(
                    "world_generation.worker_database_recovered",
                    world_id="",
                    fields={"slot": slot, "worker_id": worker_id, "requeued": requeued},
                )
            result = run_world_generation_worker_once(worker_id=worker_id, database=database)
        except DatabaseUnavailableError as exc:
            if not database_recovery_pending:
                log_world_generation_event(
                    "world_generation.worker_database_unavailable",
                    level="warning",
                    fields={"slot": slot, "worker_id": worker_id},
                    error=exc,
                )
            database_recovery_pending = True
            result = {"ok": False, "status": "database_unavailable"}
        except Exception as exc:
            _LOGGER.exception("RPG world-generation worker slot failed")
            log_world_generation_event(
                "world_generation.worker_slot_failed",
                level="error",
                fields={"slot": slot, "worker_id": worker_id},
                error=exc,
            )
            result = None
        if result is not None:
            consecutive_empty_polls = 0
            result_status = str(result.get("status") or "")
            if result_status == "retrying":
                time.sleep(_RETRY_POLL_SECONDS)
            elif result_status == "database_unavailable":
                time.sleep(_DATABASE_RECOVERY_POLL_SECONDS)
        else:
            consecutive_empty_polls += 1
        retry_after_idle_poll = False
        with state.condition:
            state.calls_in_progress -= 1
            if result is not None:
                state.completion_generation += 1
                state.condition.notify_all()
                continue
            if consecutive_empty_polls < _DAG_IDLE_POLLS_BEFORE_STOP:
                retry_after_idle_poll = True
            elif state.calls_in_progress == 0:
                state.stop = True
                state.condition.notify_all()
                return
            else:
                observed_generation = state.completion_generation
                state.condition.wait_for(
                    lambda: state.stop
                    or state.completion_generation != observed_generation
                )
                if state.stop:
                    return
                consecutive_empty_polls = 0
        if retry_after_idle_poll:
            time.sleep(_DAG_IDLE_POLL_SECONDS)


def _worker_pool_loop(database: Any | None, provider_route: str) -> None:
    global _worker_active
    worker_limit = world_generation_worker_limit(provider_route=provider_route)
    try:
        recovered = _recover_interrupted_jobs(database=database)
        state = _WorkerPoolState()
        _LOGGER.info(
            "Starting RPG world-generation worker pool with %s slot(s) for %s",
            worker_limit,
            provider_route or "configured route",
        )
        log_world_generation_event(
            "world_generation.worker_pool_started",
            fields={
                "worker_limit": worker_limit,
                "configured_provider": provider_route or _configured_world_forge_provider(),
                "recovered_orphaned_jobs": recovered["discarded"],
                "requeued_interrupted_jobs": recovered["requeued"],
            },
        )
        workers = [
            threading.Thread(
                target=_worker_loop,
                args=(slot, database, state),
                daemon=True,
                name=f"omnix-rpg-world-generation-{slot}",
            )
            for slot in range(1, worker_limit + 1)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
    except Exception as exc:
        log_world_generation_event(
            "world_generation.worker_pool_failed",
            level="error",
            fields={"worker_limit": worker_limit, "provider_route": provider_route},
            error=exc,
        )
        raise
    finally:
        log_world_generation_event(
            "world_generation.worker_pool_stopped",
            fields={"worker_limit": worker_limit, "provider_route": provider_route},
        )
        with _worker_lock:
            _worker_active = False


def kick_world_generation_worker(
    *,
    database: Any | None = None,
    provider_route: str = "",
) -> bool:
    """Start one bounded provider-aware worker pool when none is already active."""

    global _worker_active
    with _worker_lock:
        if _worker_active:
            log_world_generation_event(
                "world_generation.worker_pool_already_active",
                fields={
                    "worker_limit": world_generation_worker_limit(provider_route=provider_route),
                    "provider_route": provider_route,
                },
            )
            return False
        _worker_active = True
    thread = threading.Thread(
        target=_worker_pool_loop,
        args=(database, provider_route),
        daemon=True,
        name="omnix-rpg-world-generation-supervisor",
    )
    thread.start()
    return True
