"""Run supervision: the supervisor loop, stalled-run and orphan recovery, heartbeats (WP-8.2).

Functions over the run service; ``AgentRunService`` keeps one delegator per
function, so callers and tests that patch the service methods are unchanged.
"""
from __future__ import annotations

from .profiles import profile_produces_diff
from .event_queries import latest_event
from datetime import datetime, timedelta, timezone
import threading
from .budget import AgentBudgetError
from .contracts import (
    AgentEvent,
    AgentRunCommand,
    AgentRunSnapshot,
)
from app.observability.agent_logging import log_agent_activity
from .repository import AgentLeaseConflict
from typing import Any, TYPE_CHECKING
from .service_core import (
    _progress_idle_timeout_seconds,
    _stalled_recovery_limit,
)
from .run_repository_queries import PostgresAgentRunQueries

if TYPE_CHECKING:
    from app.platform.agent_runtime.service_core import AgentRunService


# Returned by an extracted step that did not settle its caller.
_CONTINUE = object()


def start_supervisor(service: AgentRunService) -> None:
    """Start supervision through the composed worker startup lifecycle."""
    with service._supervisor_lock:
        if service._supervisor_started:
            return
        service._supervisor_stop.clear()
        thread = threading.Thread(
            target=service._supervisor_loop,
            name="omnix-agent-supervisor",
            daemon=True,
        )
        service._supervisor_thread = thread
        service._supervisor_started = True
        thread.start()


def stop_supervisor(service: AgentRunService) -> None:
    service._supervisor_stop.set()
    with service._supervisor_lock:
        thread = service._supervisor_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=6.0)
    with service._supervisor_lock:
        service._supervisor_thread = None
        service._supervisor_started = False


def recover_orphaned_runs(service: AgentRunService) -> list[str]:
    """Re-acquire expired/unowned non-terminal runs and resume from workspace truth."""
    recovered: list[str] = []
    with service.unit_of_work(service.database) as work:
        rows = PostgresAgentRunQueries(work.connection, service.context).orphaned_run_ids().fetchall()
        work.rollback()
    for row in rows:
        run_id = str(row[0])
        snapshot = service.get(run_id)
        if snapshot is None or service.runtime.get_status(run_id) is not None:
            continue
        try:
            with service.unit_of_work(service.database) as work:
                repository = service.repository_factory(work.connection, service.context)
                lease = repository.acquire_lease(run_id, worker_id=service.worker_id, ttl_seconds=90)
                service._remember_lease(lease)
                repository.reset_processing_commands(run_id)
                current = repository.get_run(run_id)
                queued_cancel = next(
                    (
                        item
                        for item in repository.list_pending_commands(run_id)
                        if item.command_type == "cancel"
                    ),
                    None,
                )
                if current is not None and queued_cancel is not None:
                    # The dead owner never delivered this cancel. Honour it
                    # instead of restarting a runtime only to stop it.
                    if repository.claim_command(run_id, queued_cancel.command_id):
                        current = repository.update_state(
                            run_id,
                            expected_revision=current.revision,
                            status="cancelled",
                            desired_state="cancelled",
                            worker_id=service.worker_id,
                            lease_token=lease.lease_token,
                        )
                        repository.complete_command(run_id, queued_cancel.command_id)
                        service._maybe_finalize_parent_in_repository(repository, run_id)
                    work.commit()
                    service._cancel_descendants(run_id)
                    recovered.append(run_id)
                    continue
                if current is not None:
                    repository.update_state(
                        run_id,
                        expected_revision=current.revision,
                        status="starting",
                        worker_id=service.worker_id,
                        lease_token=lease.lease_token,
                    )
                work.commit()
            service.runtime.start(snapshot.spec)
            with service.unit_of_work(service.database) as work:
                repository = service.repository_factory(work.connection, service.context)
                pending = repository.list_pending_commands(run_id)
                latest_revision = repository.latest_task_revision(run_id)
                work.rollback()
            for pending_command in pending:
                current = service.command(pending_command)
                if current.status in {"completed", "failed", "cancelled"} or current.desired_state != "running":
                    break
            current = service.get(run_id)
            if current is None:
                raise RuntimeError("recovered run disappeared")
            if current.status in {"completed", "failed", "cancelled"} or current.desired_state != "running":
                recovered.append(run_id)
                continue
            recovery_payload: dict[str, Any] = {
                "message": "This run was recovered after a worker restart. Reinspect the current workspace before continuing.",
            }
            if latest_revision is not None:
                recovery_payload.update({
                    "effective_objective": latest_revision.effective_objective,
                    "evidence_policy": latest_revision.evidence_decision.policy.model_dump(mode="json"),
                    "task_revision_id": latest_revision.revision_id,
                })
            service.runtime.command(
                AgentRunCommand(
                    run_id=run_id,
                    command_type="steer",
                    payload=recovery_payload,
                )
            )
            recovered.append(run_id)
        except Exception as exc:
            service._fail_recovery(run_id, exc)
            continue
    return recovered


def _fail_recovery(service: AgentRunService, run_id: str, exc: Exception) -> None:
    service.runtime.close_run(run_id)
    with service.unit_of_work(service.database) as work:
        locked = PostgresAgentRunQueries(work.connection, service.context).lock_run(run_id).fetchone()
        if locked is None:
            work.rollback()
            return
        repository = service.repository_factory(work.connection, service.context)
        current = repository.get_run(run_id)
        if current is not None and current.status not in {"completed", "failed", "cancelled"}:
            repository.update_state(
                run_id,
                expected_revision=current.revision,
                status="failed",
                desired_state="cancelled",
                worker_id=service.worker_id,
                last_error=f"recovery_failed:{type(exc).__name__}: {exc}"[:2000],
            )
            service._maybe_finalize_parent_in_repository(repository, run_id)
        work.commit()


def _supervisor_loop(service: AgentRunService) -> None:
    while not service._supervisor_stop.is_set():
        try:
            service._supervise_once()
        except Exception as exc:
            log_agent_activity(
                "service.supervisor.loop_failed",
                category="recovery",
                level="error",
                fields={"worker_id": service.worker_id},
                error=exc,
                include_traceback=True,
            )
        service._supervisor_stop.wait(5.0)


def _supervise_once(service: AgentRunService) -> None:
    with service.unit_of_work(service.database) as work:
        rows = PostgresAgentRunQueries(work.connection, service.context).owned_unfinished_run_ids(service.worker_id).fetchall()
        work.rollback()
    for row in rows:
        run_id = str(row[0])
        try:
            service.budgets.enforce_wall_time(run_id)
        except AgentBudgetError as exc:
            log_agent_activity(
                "service.supervisor.budget_exceeded",
                category="recovery",
                level="error",
                run_id=run_id,
                fields={"worker_id": service.worker_id},
                error=exc,
            )
            service.runtime.close_run(run_id)
            service._cancel_descendants(run_id)
            continue
        try:
            service.heartbeat(run_id, ttl_seconds=90)
        except AgentLeaseConflict as exc:
            # This worker no longer owns the lease. Continuing its local Pi
            # process would violate execution authority, so stop supervising
            # this run immediately and let the current owner proceed.
            log_agent_activity(
                "service.supervisor.lease_lost",
                category="recovery",
                level="warning",
                run_id=run_id,
                fields={"worker_id": service.worker_id},
                error=exc,
            )
            service.runtime.close_run(run_id)
            continue
        except Exception as exc:
            # A transient database failure is not proof that ownership was
            # lost. Keep normal progress supervision active and retry lease
            # renewal on the next supervisor cycle.
            log_agent_activity(
                "service.supervisor.heartbeat_failed",
                category="recovery",
                level="error",
                run_id=run_id,
                fields={"worker_id": service.worker_id},
                error=exc,
                include_traceback=True,
            )
        try:
            service._supervise_stalled_run(run_id)
        except Exception as exc:
            # A transient supervisor/database failure must not take down
            # supervision for every other active run. Lease expiry and
            # orphan recovery remain the fallback safety net.
            log_agent_activity(
                "service.supervisor.stall_check_failed",
                category="recovery",
                level="error",
                run_id=run_id,
                fields={"worker_id": service.worker_id},
                error=exc,
                include_traceback=True,
            )
            continue
        try:
            service._deliver_pending_commands(run_id)
        except Exception as exc:
            log_agent_activity(
                "service.supervisor.command_delivery_failed",
                category="recovery",
                level="error",
                run_id=run_id,
                fields={"worker_id": service.worker_id},
                error=exc,
                include_traceback=True,
            )

    with service.unit_of_work(service.database) as work:
        terminal_parents = PostgresAgentRunQueries(work.connection, service.context).finished_parents_with_unfinished_children().fetchall()
        work.rollback()
    for row in terminal_parents:
        service._cancel_descendants(str(row[0]))

    active_ids = service.runtime.active_run_ids()
    if active_ids:
        with service.unit_of_work(service.database) as work:
            terminal_runtime_rows = PostgresAgentRunQueries(work.connection, service.context).finished_run_ids(list(active_ids)).fetchall()
            work.rollback()
        for row in terminal_runtime_rows:
            service.runtime.close_run(str(row[0]))

    service.recover_orphaned_runs()


def _deliver_pending_commands(service: AgentRunService, run_id: str) -> None:
    if not service._runtime_owns_run(run_id):
        return
    claimed = []
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        for command in repository.list_pending_commands(run_id, limit=20):
            if repository.claim_command(run_id, command.command_id):
                claimed.append(command)
        work.commit()
    for command in claimed:
        try:
            with service._run_lock(run_id):
                service._apply_claimed_command(command)
            with service.unit_of_work(service.database) as work:
                repository = service.repository_factory(work.connection, service.context)
                repository.complete_command(run_id, command.command_id)
                work.commit()
        except Exception as exc:
            log_agent_activity(
                "service.command.owner_delivery_failed",
                category="service",
                level="error",
                run_id=run_id,
                fields={"command_id": command.command_id, "command_type": command.command_type},
                error=exc,
                include_traceback=True,
            )
            service._mark_command_failed(command, exc)


def _supervise_stalled_run(service: AgentRunService, run_id: str) -> None:
    """Supervise a leased run whose durable activity became quiet.

    Worker heartbeats only establish that the supervisor thread is alive.
    Coding runs keep a Pi-owned loop and receive an advisory unless the
    local runtime session is conclusively absent. Other profiles retain
    bounded legacy recovery while they migrate to runtime-owned lifecycles.
    """
    log_agent_activity(
        "service.recovery.check_started",
        category="recovery",
        run_id=run_id,
        fields={
            "idle_timeout_seconds": _progress_idle_timeout_seconds(),
            "recovery_limit": _stalled_recovery_limit(),
        },
    )
    now = datetime.now(timezone.utc)
    terminalize = False
    current: AgentRunSnapshot | None = None
    attempt = 0
    progress_event: AgentEvent | None = None

    with service._run_lock(run_id):
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            current = repository.get_run(run_id)
            if (
                current is None
                or current.status not in {"running", "resume_requested"}
                or current.desired_state != "running"
            ):
                log_agent_activity(
                    "service.recovery.not_eligible",
                    category="recovery",
                    run_id=run_id,
                    fields={
                        "found": current is not None,
                        "status": current.status if current is not None else None,
                        "desired_state": current.desired_state if current is not None else None,
                    },
                )
                work.rollback()
                return
            progress_event = repository.latest_progress_event(run_id)
            progress_at = (
                progress_event.created_at
                if progress_event is not None
                else current.updated_at or current.created_at
            )
            if progress_at is None:
                log_agent_activity(
                    "service.recovery.no_checkpoint",
                    category="recovery",
                    level="warning",
                    run_id=run_id,
                    fields={"status": current.status, "revision": current.revision},
                )
                work.rollback()
                return
            if progress_at.tzinfo is None:
                progress_at = progress_at.replace(tzinfo=timezone.utc)
            if now - progress_at < timedelta(seconds=_progress_idle_timeout_seconds()):
                log_agent_activity(
                    "service.recovery.progress_current",
                    category="recovery",
                    level="debug",
                    run_id=run_id,
                    fields={
                        "last_progress_event": progress_event.event_type if progress_event else None,
                        "last_progress_sequence": progress_event.sequence if progress_event else None,
                        "last_progress_at": progress_at.isoformat(),
                        "idle_seconds": round((now - progress_at).total_seconds(), 3),
                    },
                )
                work.rollback()
                return

            get_runtime_status = getattr(service.runtime, "get_status", None)
            runtime_confirmed_missing = (
                callable(get_runtime_status) and get_runtime_status(run_id) is None
            )
            outcome = _record_stall_advisory(current, runtime_confirmed_missing, repository, run_id, progress_event, now, progress_at, work)
            if outcome is not _CONTINUE:
                return outcome

            attempt = repository.count_events(run_id, "run.recovery_requested") + 1
            quality_stage = None
            if callable(getattr(repository, "list_events", None)):
                stage_event = latest_event(repository, run_id, "quality.stage")
                if stage_event is not None:
                    quality_stage = str(stage_event.payload.get("stage") or "").strip() or None
            reason = (
                f"no durable agent progress for {int((now - progress_at).total_seconds())}s"
                f" after {progress_event.event_type if progress_event else 'run start'}"
            )
            log_agent_activity(
                "service.recovery.stall_detected",
                category="recovery",
                level="warning",
                run_id=run_id,
                fields={
                    "attempt": attempt,
                    "reason": reason,
                    "last_progress_event": progress_event.event_type if progress_event else None,
                    "last_progress_sequence": progress_event.sequence if progress_event else None,
                    "last_progress_at": progress_at.isoformat(),
                    "idle_seconds": round((now - progress_at).total_seconds(), 3),
                    "quality_stage": quality_stage,
                },
            )
            current, terminalize = _request_stall_recovery(attempt, run_id, reason, quality_stage, repository, current, service, progress_event, terminalize)
            work.commit()

        if terminalize:
            log_agent_activity(
                "service.recovery.terminalizing",
                category="recovery",
                level="error",
                run_id=run_id,
                fields={"attempt": attempt},
            )
            service.runtime.close_run(run_id)
            service._cancel_descendants(run_id)
            return

        assert current is not None
        log_agent_activity(
            "service.recovery.dispatching_resume",
            category="recovery",
            level="warning",
            run_id=run_id,
            fields={"attempt": attempt, "quality_stage": quality_stage},
        )
        _dispatch_stall_recovery(quality_stage, attempt, service, run_id, current)


def _record_stall_advisory(current, runtime_confirmed_missing, repository, run_id, progress_event, now, progress_at, work):
    """A coding run keeps its Pi loop: record one advisory per progress checkpoint instead of restarting it."""
    if profile_produces_diff(current.spec.profile) and not runtime_confirmed_missing:
        # Pi owns the complete coding loop. A quiet model/tool turn
        # is not proof that its process died, and restarting it
        # destroys the context it needs to finish efficiently.
        # Persist one advisory warning per progress checkpoint and
        # leave interruption/recovery to an explicit user command.
        prior_warning = None
        if callable(getattr(repository, "list_events", None)):
            prior_warning = latest_event(repository, run_id, "run.stall_suspected")
        progress_sequence = progress_event.sequence if progress_event else None
        warned_sequence = (
            prior_warning.payload.get("last_progress_sequence")
            if prior_warning is not None
            else None
        )
        if prior_warning is None or warned_sequence != progress_sequence:
            reason = (
                f"no durable agent activity for {int((now - progress_at).total_seconds())}s"
                f" after {progress_event.event_type if progress_event else 'run start'}"
            )
            repository.append_event(AgentEvent(
                run_id=run_id,
                event_type="run.stall_suspected",
                payload={
                    "reason": reason,
                    "idle_seconds": round((now - progress_at).total_seconds(), 3),
                    "last_progress_event": progress_event.event_type if progress_event else None,
                    "last_progress_sequence": progress_sequence,
                    "automatic_recovery": False,
                },
            ))
            log_agent_activity(
                "service.recovery.stall_advisory_recorded",
                category="recovery",
                level="warning",
                run_id=run_id,
                fields={"reason": reason, "last_progress_sequence": progress_sequence},
            )
            work.commit()
        else:
            work.rollback()
        return
    return _CONTINUE


def _request_stall_recovery(attempt, run_id, reason, quality_stage, repository, current, service, progress_event, terminalize):
    """Request a bounded recovery, or fail the run once the recovery limit is spent."""
    if attempt > _stalled_recovery_limit():
        terminalize = True
        log_agent_activity(
            "service.recovery.exhausted",
            category="recovery",
            level="error",
            run_id=run_id,
            fields={
                "attempt": attempt,
                "recovery_limit": _stalled_recovery_limit(),
                "reason": reason,
                "quality_stage": quality_stage,
            },
        )
        repository.append_event(AgentEvent(
            run_id=run_id,
            event_type="run.recovery_failed",
            payload={
                "attempt": attempt,
                "reason": reason,
                "recovery_limit": _stalled_recovery_limit(),
            },
        ))
        repository.update_state(
            run_id,
            expected_revision=current.revision,
            status="failed",
            desired_state="cancelled",
            worker_id=service.worker_id,
            last_error=f"stalled_run:{reason}; recovery limit exhausted"[:2000],
        )
    else:
        log_agent_activity(
            "service.recovery.requested",
            category="recovery",
            level="warning",
            run_id=run_id,
            fields={
                "attempt": attempt,
                "recovery_limit": _stalled_recovery_limit(),
                "reason": reason,
                "quality_stage": quality_stage,
            },
        )
        repository.append_event(AgentEvent(
            run_id=run_id,
            event_type="run.recovery_requested",
            payload={
                "attempt": attempt,
                "reason": reason,
                "last_progress_event": progress_event.event_type if progress_event else None,
                "last_progress_sequence": progress_event.sequence if progress_event else None,
                "quality_stage": quality_stage,
            },
        ))
        current = repository.update_state(
            run_id,
            expected_revision=current.revision,
            status="resume_requested",
            desired_state="running",
            worker_id=service.worker_id,
        )
    return current, terminalize


def _dispatch_stall_recovery(quality_stage, attempt, service, run_id, current):
    """Restart or resume the runtime with the recovery prompt; fail the run when that fails."""
    recovery_message = (
        "The previous runtime stopped producing progress. The workspace and durable task "
        "state are authoritative. Resume the current task from the existing workspace, "
        "inspect the last failed or incomplete operation, and continue without changing scope. "
        "The task and objective are already authoritative; do not ask the user to restate the "
        "request or wait for clarification. "
        + (
            "This is an internal quality/self-review turn that did not finish its protocol. "
            "Do not modify files or ask the user a question; inspect the current final state and "
            "return ONLY the required structured verdict JSON, even if the verdict is blocked. "
            if quality_stage == "self_review"
            else "If this is an internal quality/self-review turn, return its required structured verdict exactly, even if the verdict is blocked. "
        )
        + f"This is automatic recovery attempt {attempt}."
    )
    get_runtime_status = getattr(service.runtime, "get_status", None)
    runtime_status = (
        get_runtime_status(run_id)
        if callable(get_runtime_status)
        else None
    )
    reuse_active_session = quality_stage == "self_review" and runtime_status is not None
    if not reuse_active_session:
        service.runtime.close_run(run_id)
    try:
        if not reuse_active_session:
            service.runtime.start(current.spec)
        service.runtime.command(AgentRunCommand(
            run_id=run_id,
            command_type="resume",
            payload={"message": recovery_message, "recovery_attempt": attempt},
            idempotency_key=f"stalled-recovery:{run_id}:{attempt}",
        ))
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            persisted = repository.get_run(run_id)
            if persisted is not None and persisted.status not in {"completed", "failed", "cancelled"}:
                repository.update_state(
                    run_id,
                    expected_revision=persisted.revision,
                    status="running",
                    desired_state="running",
                    worker_id=service.worker_id,
                )
            work.commit()
        log_agent_activity(
            "service.recovery.resume_dispatched",
            category="recovery",
            level="warning",
            run_id=run_id,
            fields={
                "attempt": attempt,
                "reused_active_session": reuse_active_session,
            },
        )
    except Exception as exc:
        log_agent_activity(
            "service.recovery.resume_failed",
            category="recovery",
            level="error",
            run_id=run_id,
            fields={
                "attempt": attempt,
                "reused_active_session": reuse_active_session,
            },
            error=exc,
            include_traceback=True,
        )
        service.runtime.close_run(run_id)
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            persisted = repository.get_run(run_id)
            if persisted is not None and persisted.status not in {"completed", "failed", "cancelled"}:
                repository.append_event(AgentEvent(
                    run_id=run_id,
                    event_type="run.recovery_failed",
                    payload={"attempt": attempt, "reason": f"{type(exc).__name__}: {exc}"[:2000]},
                ))
                repository.update_state(
                    run_id,
                    expected_revision=persisted.revision,
                    status="failed",
                    desired_state="cancelled",
                    worker_id=service.worker_id,
                    last_error=f"stalled_recovery_failed:{type(exc).__name__}: {exc}"[:2000],
                )
            work.commit()
        service._cancel_descendants(run_id)


def heartbeat(service: AgentRunService, run_id: str, *, ttl_seconds: int = 60) -> None:
    log_agent_activity(
        "service.heartbeat.requested",
        category="recovery",
        run_id=run_id,
        fields={"ttl_seconds": ttl_seconds, "worker_id": service.worker_id},
    )
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        lease = repository.renew_lease(
            run_id,
            worker_id=service.worker_id,
            ttl_seconds=ttl_seconds,
        )
        service._remember_lease(lease)
        work.commit()
    log_agent_activity(
        "service.heartbeat.renewed",
        category="recovery",
        run_id=run_id,
        fields={
            "worker_id": service.worker_id,
            "lease_revision": lease.revision,
            "lease_expires_at": lease.lease_expires_at.isoformat(),
        },
    )
