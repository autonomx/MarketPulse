"""Deferred RPG narration as durable jobs (``rpg.narration``, WP-5.6).

A turn, an ``rpg.turn`` job or an ambient conversation beat that defers its
narration enqueues one job per (session, narration turn, kind); the jobs
system owns the claim, the attempts and the retries that the session JSON
queue and its in-process worker thread used to keep. The job runs
``run_narration_request``: the artifact still lands in the session, which is
the game's record of what was narrated, and its events reach every replica
through the PostgreSQL event log.
"""
from __future__ import annotations

from typing import Any

from app.apps.rpg.session.jobs.handlers import RpgNarrationJobInput
from app.jobs.models import CompleteJobRequest, CreateJobRequest, FailJobRequest, JobRecord, ResourceClass

JOB_TYPE = "rpg.narration"
MAX_ATTEMPTS = 3
_PRIORITY_BY_KIND = {"player_turn": 100, "ambient_conversation": 20, "grounding_soft_audit": 10}


def enqueue_rpg_narration(
    session_id: str,
    turn_id: str,
    narration_request: dict[str, Any],
    *,
    interaction_id: str = "",
    tick: int = 0,
    job_kind: str = "player_turn",
    job_store: Any = None,
) -> dict[str, Any]:
    """Queue the narration of ``turn_id`` once; a repeat returns the same job."""
    from app.jobs.store import default_job_store

    store = job_store if job_store is not None else default_job_store()
    kind = str(job_kind or "player_turn")
    payload = RpgNarrationJobInput(
        session_id=session_id,
        turn_id=turn_id,
        interaction_id=interaction_id,
        tick=int(tick or 0),
        job_kind=kind,
        narration_request=dict(narration_request),
    )
    job = store.create_job_once(
        CreateJobRequest(
            module="rpg",
            type=JOB_TYPE,
            resource_class=ResourceClass.GPU_LLM,
            priority=_PRIORITY_BY_KIND.get(kind, 50),
            input_ref={"session_id": session_id, "turn_id": turn_id},
            input_payload=payload.model_dump(mode="json"),
        ),
        idempotency_key=f"{JOB_TYPE}:{session_id}:{turn_id}:{kind}",
    )
    status = getattr(job.status, "value", job.status)
    return {"job_id": job.id, "turn_id": turn_id, "job_kind": kind, "status": str(status)}


class NarrationGenerationError(RuntimeError):
    """Generation failed in a way another attempt may not."""


def execute_rpg_narration_job(job_store: Any, job: JobRecord) -> JobRecord:
    """Run one narration request and apply its outcome to the turn's interaction."""
    from app.apps.rpg.session.interaction_lifecycle import apply_narration_result_to_interaction
    from app.apps.rpg.session.narration_jobs import publish_narration_failure, run_narration_request

    request = RpgNarrationJobInput.model_validate(job.input_payload or {})
    attempt = max(1, int(getattr(job, "_attempt_count", 0) or 1))
    job_store.mark_running(job.id)
    try:
        outcome = run_narration_request(
            request.session_id,
            request.turn_id,
            request.narration_request,
            job_kind=request.job_kind,
            tick=request.tick,
            attempt=attempt,
            max_attempts=MAX_ATTEMPTS,
        )
    except Exception as exc:
        final = attempt >= MAX_ATTEMPTS
        if final:
            error = str(exc) or type(exc).__name__
            publish_narration_failure(request.session_id, request.turn_id, error, attempt, MAX_ATTEMPTS)
            apply_narration_result_to_interaction(
                request.session_id,
                {"status": "failed", "turn_id": request.turn_id,
                 "interaction_id": request.interaction_id, "error": error},
            )
        failed = job_store.fail_job(
            job.id,
            FailJobRequest(
                code="rpg_narration_failed",
                message=str(exc) or "RPG narration failed",
                retryable=not final,
                details={"session_id": request.session_id, "turn_id": request.turn_id, "attempt": attempt},
            ),
        )
        return failed or job
    if request.job_kind == "player_turn":
        outcome = apply_narration_result_to_interaction(
            request.session_id, {**outcome, "interaction_id": request.interaction_id},
        )
    completed = job_store.complete_job(
        job.id,
        CompleteJobRequest(
            output_refs=[{
                "type": "rpg_narration",
                "module": "rpg",
                "session_id": request.session_id,
                "turn_id": request.turn_id,
                "status": str(outcome.get("status") or ""),
            }],
        ),
    )
    return completed or job


__all__ = [
    "JOB_TYPE",
    "MAX_ATTEMPTS",
    "NarrationGenerationError",
    "RpgNarrationJobInput",
    "enqueue_rpg_narration",
    "execute_rpg_narration_job",
]
