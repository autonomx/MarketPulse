"""Record direct foreground RPG turns without scheduling a second execution."""
from __future__ import annotations

import logging
from app.config.env import env_str as _env_str

import uuid
from contextlib import ExitStack
from copy import deepcopy
from typing import Any, Callable

from app.apps.rpg.session.foreground_turn_record import (
    FOREGROUND_TURN_RECORD_VERSION,
    build_foreground_turn_record,
)
from app.apps.rpg.session.jobs.turn_job_guard import RPG_FOREGROUND_RECORD_TYPE
from app.apps.rpg.session.jobs.foreground_context import DIRECT_RPG_SUBMISSION_ID as _DIRECT_RPG_SUBMISSION_ID
from app.apps.rpg.foundation.performance_trace import rpg_pipeline_span
from app.apps.rpg.narration.presentation.visible_response import visible_response_text

logger = logging.getLogger(__name__)

def execute_turn_with_job_mirror(
    apply_turn: Callable[..., dict[str, Any]],
    session_id: str,
    command: str,
    *args: Any,
    submission_id: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    from app.jobs.models import CompleteJobRequest, CreateJobRequest, FailJobRequest, JobStatus, ResourceClass
    from app.apps.rpg.session.jobs.foreground_submission_store import submission_store_for_job_store
    from app.jobs.store import default_job_store

    resolved_submission_id = str(submission_id or f"submit:{uuid.uuid4().hex}").strip()
    execution_scope = ExitStack()
    submission_token = _DIRECT_RPG_SUBMISSION_ID.set(resolved_submission_id)
    try:
        store = default_job_store()
        durable_store = submission_store_for_job_store(store)
        with rpg_pipeline_span("turn.idempotency_claim") as claim_span:
            durable_claim, durable_replay = _acquire_durable_claim(
                durable_store,
                session_id=session_id,
                submission_id=resolved_submission_id,
            )
            claim_span["durable"] = durable_store is not None
            claim_span["owner"] = getattr(durable_claim, "owner", None)
            claim_span["replay"] = durable_replay is not None
        if durable_replay is not None:
            return durable_replay

        with rpg_pipeline_span("turn.idempotency_job_lookup") as lookup_span:
            existing = _find_submission_record(store, session_id, resolved_submission_id)
            recovered = _recover_completed_result(existing)
            lookup_span["record_found"] = existing is not None
            lookup_span["recovered"] = recovered is not None
        if recovered is not None:
            _complete_durable_claim(durable_store, durable_claim, recovered)
            return recovered

        with rpg_pipeline_span("turn.foreground_record_create") as create_span:
            job = store.create_job(
                CreateJobRequest(
                    module="rpg",
                    type=RPG_FOREGROUND_RECORD_TYPE,
                    resource_class=ResourceClass.CPU,
                    priority=0,
                    input_ref={"session_id": session_id},
                    input_payload={
                        "command": command,
                        "player_input": command,
                        "submission_id": resolved_submission_id,
                        "determinism_policy": "replay_preserving",
                        "source": "direct_foreground_route",
                    },
                    compat={
                        "direct_foreground_route": True,
                        "foreground_record": True,
                        "record_only": True,
                    },
                )
            )
            create_span["job_id"] = job.id
            create_span["job_status"] = str(getattr(job.status, "value", job.status))
        if durable_store is not None and durable_claim is not None and durable_claim.claim_token:
            with rpg_pipeline_span("turn.foreground_record_attach") as attach_span:
                attached = durable_store.attach_job(
                    session_id,
                    resolved_submission_id,
                    durable_claim.claim_token,
                    job.id,
                )
                attach_span["attached"] = attached
            if not attached:
                return _wait_after_lost_claim(
                    durable_store,
                    session_id=session_id,
                    submission_id=resolved_submission_id,
                )
        if job.status == JobStatus.COMPLETED:
            recovered = _recover_completed_result(job)
            if recovered is not None:
                _complete_durable_claim(durable_store, durable_claim, recovered)
                return recovered

        with rpg_pipeline_span("turn.authoritative_execution_fence") as fence_span:
            ownership_replay = _begin_durable_execution(
                durable_store,
                durable_claim,
                session_id=session_id,
                submission_id=resolved_submission_id,
            )
            fence_span["replay"] = ownership_replay is not None
        if ownership_replay is not None:
            return ownership_replay
        if hasattr(store, "database") and durable_claim is not None and durable_claim.claim_token:
            from app.jobs.foreground_execution import ForegroundExecution, foreground_execution

            execution_scope.enter_context(foreground_execution(ForegroundExecution(
                workspace_id=store.context.workspace_id,
                session_id=session_id,
                submission_id=resolved_submission_id,
                job_id=job.id,
                claim_token=durable_claim.claim_token,
            )))
        with rpg_pipeline_span("turn.foreground_record_running"):
            running = store.mark_running(job.id) or job

        try:
            result = apply_turn(session_id, command, *args, **kwargs)
        except Exception as exc:
            with rpg_pipeline_span("turn.foreground_record_fail"):
                store.fail_job(
                    running.id,
                    FailJobRequest(
                        code="direct_rpg_turn_failed",
                        message=str(exc) or "Direct RPG turn failed",
                        retryable=False,
                        details={
                            "session_id": session_id,
                            "submission_id": resolved_submission_id,
                            "command": command,
                        },
                    ),
                )
                _fail_durable_claim(durable_store, durable_claim, str(exc) or "Direct RPG turn failed")
            raise

        if result.get("ok") is not True:
            error = str(result.get("error") or "direct_rpg_turn_failed")
            with rpg_pipeline_span("turn.foreground_record_fail"):
                store.fail_job(
                    running.id,
                    FailJobRequest(
                        code=error,
                        message=error,
                        retryable=False,
                        details={
                            "session_id": session_id,
                            "submission_id": resolved_submission_id,
                            "command": command,
                        },
                    ),
                )
            finalized = dict(result)
            finalized["submission_id"] = resolved_submission_id
            durable_record = build_foreground_turn_record(
                finalized,
                session_id=session_id,
                submission_id=resolved_submission_id,
                command=command,
            )
            _complete_durable_claim(durable_store, durable_claim, durable_record)
            return finalized

        content = visible_response_text(result, command) or f"Your command is accepted: {command}."
        turn_record = build_foreground_turn_record(
            result,
            session_id=session_id,
            submission_id=resolved_submission_id,
            command=command,
        )
        with rpg_pipeline_span("turn.foreground_record_finalize") as finalize_span:
            completed = store.complete_job(
                running.id,
                CompleteJobRequest(
                    output_refs=[
                        {
                            "type": "rpg_turn_response",
                            "module": "rpg",
                            "title": command[:80] or "RPG turn",
                            "content": content,
                            "session_id": session_id,
                            "submission_id": resolved_submission_id,
                            "command": command,
                            "record_version": FOREGROUND_TURN_RECORD_VERSION,
                            "turn_response": turn_record,
                            "source": "direct_foreground_route",
                        }
                    ],
                    logs=[
                        {
                            "level": "info",
                            "message": "RPG turn recorded by direct foreground route",
                            "content": content,
                            "session_id": session_id,
                            "submission_id": resolved_submission_id,
                        }
                    ],
                ),
            )
            finalize_span["job_id"] = completed.id if completed is not None else running.id
        result = dict(result)
        result["submission_id"] = resolved_submission_id
        if completed is not None:
            result["foreground_job"] = completed.model_dump(mode="json")
            result["creation_server_trace"] = {
                "server_job_created_at": completed.created_at,
                "server_job_started_at": completed.started_at,
                "server_job_completed_at": completed.completed_at,
                "server_response_persisted_at": completed.completed_at,
                "job_id": completed.id,
                "submission_id": resolved_submission_id,
            }
        durable_record = build_foreground_turn_record(
            result,
            session_id=session_id,
            submission_id=resolved_submission_id,
            command=command,
        )
        with rpg_pipeline_span("turn.idempotency_result_finalize"):
            _complete_durable_claim(durable_store, durable_claim, durable_record)
        return result
    finally:
        execution_scope.close()
        _DIRECT_RPG_SUBMISSION_ID.reset(submission_token)


_apply_turn_with_job_mirror = execute_turn_with_job_mirror


def _find_submission_record(store: Any, session_id: str, submission_id: str) -> Any | None:
    targeted_lookup = getattr(store, "find_job_by_submission", None)
    if callable(targeted_lookup):
        try:
            return targeted_lookup(
                job_type=RPG_FOREGROUND_RECORD_TYPE,
                session_id=session_id,
                submission_id=submission_id,
            )
        except Exception:
            logger.debug("suppressed error in %s", "_find_submission_record", exc_info=True)
            return None
    try:
        jobs = store.iter_jobs(job_types=(RPG_FOREGROUND_RECORD_TYPE,))
    except Exception:
        logger.debug("suppressed error in %s", "_find_submission_record", exc_info=True)
        return None
    for job in jobs:
        if getattr(job, "type", "") != RPG_FOREGROUND_RECORD_TYPE:
            continue
        input_ref = getattr(job, "input_ref", None)
        payload = getattr(job, "input_payload", None)
        if not isinstance(input_ref, dict) or not isinstance(payload, dict):
            continue
        if str(input_ref.get("session_id") or "") != session_id:
            continue
        if str(payload.get("submission_id") or "") == submission_id:
            return job
    return None


def _recover_completed_result(job: Any | None) -> dict[str, Any] | None:
    if job is None or str(getattr(getattr(job, "status", None), "value", getattr(job, "status", ""))) != "completed":
        return None
    output_refs = getattr(job, "output_refs", None)
    if not isinstance(output_refs, list) or not output_refs:
        return None
    first = output_refs[0] if isinstance(output_refs[0], dict) else {}
    record = first.get("turn_response")
    if not isinstance(record, dict):
        return None
    recovered = deepcopy(record)
    submission_id = str(first.get("submission_id") or "")
    recovered["submission_id"] = submission_id
    recovered["foreground_job"] = job.model_dump(mode="json")
    recovered["idempotent_replay"] = True
    return recovered


def _acquire_durable_claim(
    durable_store: Any,
    *,
    session_id: str,
    submission_id: str,
) -> tuple[Any | None, dict[str, Any] | None]:
    if durable_store is None:
        return None, None
    claim = durable_store.claim(
        session_id,
        submission_id,
        lease_seconds=_submission_lease_seconds(),
    )
    if not claim.owner and claim.status == "claimed":
        claim = durable_store.wait_for_terminal_or_claim(
            session_id,
            submission_id,
            timeout_seconds=_submission_wait_seconds(),
            lease_seconds=_submission_lease_seconds(),
        )
    recovered = _recover_durable_result(claim)
    if recovered is not None:
        return claim, recovered
    if getattr(claim, "status", "") == "failed":
        raise RuntimeError(getattr(claim, "error", None) or "foreground submission failed")
    if getattr(claim, "owner", False):
        return claim, None
    raise TimeoutError(
        f"foreground submission {submission_id} for {session_id} did not reach a terminal state"
    )


def _begin_durable_execution(
    durable_store: Any,
    claim: Any,
    *,
    session_id: str,
    submission_id: str,
) -> dict[str, Any] | None:
    token = getattr(claim, "claim_token", None)
    if durable_store is None or not token:
        return None
    if durable_store.mark_execution_started(session_id, submission_id, token):
        return None
    return _wait_after_lost_claim(
        durable_store,
        session_id=session_id,
        submission_id=submission_id,
    )


def _wait_after_lost_claim(
    durable_store: Any,
    *,
    session_id: str,
    submission_id: str,
) -> dict[str, Any]:
    terminal = durable_store.wait_for_terminal(
        session_id,
        submission_id,
        timeout_seconds=_submission_wait_seconds(),
    )
    recovered = _recover_durable_result(terminal)
    if recovered is not None:
        return recovered
    if getattr(terminal, "status", "") == "failed":
        raise RuntimeError(getattr(terminal, "error", None) or "foreground submission failed")
    raise TimeoutError(
        f"foreground submission ownership changed before execution for {submission_id}"
    )


def _recover_durable_result(claim: Any) -> dict[str, Any] | None:
    if getattr(claim, "status", "") != "completed":
        return None
    raw = getattr(claim, "result", None)
    if not isinstance(raw, dict):
        return None
    recovered = deepcopy(raw)
    recovered["submission_id"] = str(getattr(claim, "submission_id", "") or recovered.get("submission_id") or "")
    recovered["idempotent_replay"] = True
    return recovered


def _complete_durable_claim(durable_store: Any, claim: Any, result: dict[str, Any]) -> None:
    token = getattr(claim, "claim_token", None)
    if durable_store is None or not token:
        return
    durable_store.complete(claim.session_id, claim.submission_id, token, result)


def _fail_durable_claim(durable_store: Any, claim: Any, error: str) -> None:
    token = getattr(claim, "claim_token", None)
    if durable_store is None or not token:
        return
    durable_store.fail(claim.session_id, claim.submission_id, token, error)


def _submission_wait_seconds() -> float:
    raw = _env_str("OMNIX_RPG_SUBMISSION_WAIT_SECONDS", "120")
    try:
        return max(0.1, min(600.0, float(raw)))
    except (TypeError, ValueError):
        return 120.0


def _submission_lease_seconds() -> float:
    raw = _env_str("OMNIX_RPG_SUBMISSION_LEASE_SECONDS", "30")
    try:
        return max(0.1, min(600.0, float(raw)))
    except (TypeError, ValueError):
        return 30.0
