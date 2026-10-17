"""Run lifecycle: start, child starts, launch, promotion jobs and terminal cleanup (WP-8.2).

Functions over the run service; ``AgentRunService`` keeps one delegator per
function, so callers and tests that patch the service methods are unchanged.
"""
from __future__ import annotations

from .budget import apply_default_run_limits
from .contracts import (
    AgentRunSnapshot,
    AgentRunSpec,
)
from app.observability.agent_logging import log_agent_activity
from .repository import PostgresAgentRunRepository
from typing import TYPE_CHECKING, cast
from .service_core import (
    _LeaseBoundRunRepository,
)
from .run_repository_queries import PostgresAgentRunQueries

if TYPE_CHECKING:
    from app.platform.agent_runtime.service_core import AgentRunService


def _close_terminal_runtime(service: AgentRunService, run_id: str) -> None:
    """Stop a local runtime as soon as durable state becomes terminal."""

    runtime = getattr(service, "runtime", None)
    lease_lock = getattr(service, "_lease_token_lock", None)
    lease_tokens = getattr(service, "_lease_tokens", None)
    if lease_lock is not None and lease_tokens is not None:
        with lease_lock:
            lease_tokens.pop(run_id, None)
    close_run = getattr(runtime, "close_run", None)
    if not callable(close_run):
        return
    try:
        close_run(run_id)
    except Exception as exc:
        log_agent_activity(
            "service.runtime_terminal_cleanup_failed",
            category="service",
            level="error",
            run_id=run_id,
            fields={"worker_id": service.worker_id},
            error=exc,
            include_traceback=True,
        )


def start_with_context(
    service: AgentRunService,
    spec: AgentRunSpec,
    *,
    reference_context: str = "",
    reference_images: list[dict[str, str]] | None = None,
) -> AgentRunSnapshot:
    """Start a run with ephemeral Chat reference context and images.

    Reference context and image payloads are intentionally not written into
    AgentRunSpec or task revisions, so Chat retention and forget semantics
    remain owned by the Chat subsystem.
    """

    spec = apply_default_run_limits(service._prepare_start_spec(spec))

    log_agent_activity(
        "service.start.requested",
        category="service",
        run_id=spec.run_id,
        fields={
            "profile": spec.profile,
            "task": spec.task,
            "objective": spec.objective,
            "provider_id": spec.model.provider_id,
            "model_id": spec.model.model_id,
            "capabilities": list(spec.capabilities),
            "has_reference_context": bool(reference_context),
            "reference_image_count": len(reference_images or []),
        },
    )
    try:
        service._validate_run_spec_authority(spec)
        service._validate_evidence_authority(spec)
    except Exception as exc:
        log_agent_activity(
            "service.start.validation_failed",
            category="service",
            level="error",
            run_id=spec.run_id,
            fields={"profile": spec.profile},
            error=exc,
            include_traceback=True,
        )
        raise
    try:
        issued = service._prepare_workspace(service._bind_github_repository_authority(spec))
    except Exception as exc:
        log_agent_activity(
            "service.start.workspace_failed",
            category="service",
            level="error",
            run_id=spec.run_id,
            fields={"profile": spec.profile},
            error=exc,
            include_traceback=True,
        )
        raise
    log_agent_activity(
        "service.start.workspace_prepared",
        category="service",
        run_id=issued.run_id,
        fields={"workspace": issued.workspace.model_dump(mode="json") if issued.workspace else None},
    )
    try:
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            snapshot = service._persist_starting_run(repository, issued)
            work.commit()
    except Exception as exc:
        log_agent_activity(
            "service.start.persistence_failed",
            category="service",
            level="error",
            run_id=issued.run_id,
            fields={"workspace": issued.workspace.model_dump(mode="json") if issued.workspace else None},
            error=exc,
            include_traceback=True,
        )
        raise
    log_agent_activity(
        "service.start.persisted",
        category="service",
        run_id=issued.run_id,
        fields={"status": snapshot.status, "revision": snapshot.revision},
    )
    if reference_context or reference_images:
        return service._launch_runtime(
            issued,
            snapshot,
            reference_context=reference_context,
            **({"reference_images": reference_images} if reference_images else {}),
        )
    return service._launch_runtime(issued, snapshot)


def submit_start(
    service: AgentRunService,
    spec: AgentRunSpec,
    *,
    job_store,
    reference_session_id: str | None = None,
    reference_message_id: str | None = None,
    reference_message_ids: list[str] | None = None,
    task_graph_run_id: str | None = None,
    task_graph_node_ids: list[str] | None = None,
) -> AgentRunSnapshot:
    """Persist a queued run and ask the durable worker to prepare it."""
    from .jobs import create_agent_workspace_prepare_request, enqueue_agent_job

    issued = apply_default_run_limits(service._prepare_start_spec(spec))
    service._validate_run_spec_authority(issued)
    service._validate_evidence_authority(issued)
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        snapshot = repository.create_run(issued)
        work.commit()
    try:
        enqueue_agent_job(
            job_store,
            create_agent_workspace_prepare_request(
                issued.run_id,
                reference_session_id=reference_session_id,
                reference_message_id=reference_message_id,
                reference_message_ids=reference_message_ids,
                task_graph_run_id=task_graph_run_id,
                task_graph_node_ids=task_graph_node_ids,
            ),
            idempotency_key=f"run:{issued.run_id}:workspace-prepare",
        )
    except Exception as exc:
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            current = repository.get_run(issued.run_id)
            if current is not None and current.status == "queued":
                repository.update_state(
                    issued.run_id,
                    expected_revision=current.revision,
                    status="failed",
                    desired_state="cancelled",
                    last_error=f"agent_start_enqueue_failed:{type(exc).__name__}: {exc}"[:2000],
                )
            work.commit()
        raise
    return snapshot


def prepare_workspace_job(
    service: AgentRunService,
    run_id: str,
    *,
    job_store,
    reference_session_id: str | None = None,
    reference_message_id: str | None = None,
    reference_message_ids: list[str] | None = None,
    task_graph_run_id: str | None = None,
    task_graph_node_ids: list[str] | None = None,
) -> AgentRunSnapshot:
    """Prepare a queued run's workspace in a durable worker process."""
    from .jobs import create_agent_run_start_request, enqueue_agent_job

    snapshot = service.get(run_id)
    if snapshot is None:
        raise KeyError(run_id)
    if snapshot.status in {"completed", "failed", "cancelled"}:
        return snapshot
    prepared = service._prepare_workspace(
        service._bind_github_repository_authority(snapshot.spec)
    )
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        current = repository.get_run(run_id)
        if current is None:
            raise KeyError(run_id)
        if current.status in {"completed", "failed", "cancelled"}:
            work.rollback()
            return current
        if current.spec != prepared:
            current = repository.update_spec(
                run_id,
                expected_revision=current.revision,
                spec=prepared,
            )
        service._capture_workspace_baseline(repository, prepared)
        work.commit()
    enqueue_agent_job(
        job_store,
        create_agent_run_start_request(
            run_id,
            reference_session_id=reference_session_id,
            reference_message_id=reference_message_id,
            reference_message_ids=reference_message_ids,
            task_graph_run_id=task_graph_run_id,
            task_graph_node_ids=task_graph_node_ids,
        ),
        idempotency_key=f"run:{run_id}:start",
    )
    return current


def start_prepared_run(
    service: AgentRunService,
    run_id: str,
    *,
    reference_context: str = "",
    reference_images: list[dict[str, str]] | None = None,
) -> AgentRunSnapshot:
    """Acquire run ownership and start Pi after workspace preparation."""
    snapshot = service.get(run_id)
    if snapshot is None:
        raise KeyError(run_id)
    if snapshot.status in {"completed", "failed", "cancelled"}:
        return snapshot
    if service._runtime_owns_run(run_id):
        return snapshot
    service._validate_run_spec_authority(snapshot.spec)
    service._validate_evidence_authority(snapshot.spec)
    if (
        snapshot.spec.workspace is not None
        and snapshot.spec.workspace.repository
        and snapshot.spec.workspace.isolation_policy == "supervised_worktree"
        and not snapshot.spec.workspace.worktree
    ):
        raise RuntimeError("agent workspace preparation has not completed")
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        current = repository.get_run(run_id)
        if current is None:
            raise KeyError(run_id)
        if current.status in {"completed", "failed", "cancelled"}:
            work.rollback()
            return current
        lease = repository.acquire_lease(run_id, worker_id=service.worker_id, ttl_seconds=90)
        service._remember_lease(lease)
        starting = repository.update_state(
            run_id,
            expected_revision=current.revision,
            status="starting",
            worker_id=service.worker_id,
            lease_token=lease.lease_token,
        )
        work.commit()
    return service._launch_runtime(
        current.spec,
        starting,
        reference_context=reference_context,
        **({"reference_images": reference_images} if reference_images else {}),
    )


def start_child(service: AgentRunService, parent_run_id: str, request) -> AgentRunSnapshot:
    child_spec, _ = service._create_child_start(parent_run_id, request)
    prepared = service._prepare_workspace(
        service._bind_github_repository_authority(child_spec)
    )
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        current = repository.get_run(child_spec.run_id)
        if current is None:
            raise KeyError(child_spec.run_id)
        if current.spec != prepared:
            repository.update_spec(
                child_spec.run_id,
                expected_revision=current.revision,
                spec=prepared,
            )
        snapshot = service._persist_starting_run(repository, prepared)
        work.commit()
    return service._launch_runtime(prepared, snapshot)


def submit_child_start(service: AgentRunService, parent_run_id: str, request, *, job_store) -> AgentRunSnapshot:
    """Reserve a child under its parent lock, then defer workspace work."""
    from .jobs import create_agent_workspace_prepare_request, enqueue_agent_job

    child_spec, snapshot = service._create_child_start(parent_run_id, request)
    try:
        enqueue_agent_job(
            job_store,
            create_agent_workspace_prepare_request(child_spec.run_id),
            idempotency_key=f"run:{child_spec.run_id}:workspace-prepare",
        )
    except Exception as exc:
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            current = repository.get_run(child_spec.run_id)
            if current is not None and current.status == "queued":
                repository.update_state(
                    child_spec.run_id,
                    expected_revision=current.revision,
                    status="failed",
                    desired_state="cancelled",
                    last_error=f"agent_child_enqueue_failed:{type(exc).__name__}: {exc}"[:2000],
                )
            work.commit()
        raise
    return snapshot


def _enqueue_promote_job(service: AgentRunService, run_id: str, *, trigger_id: str) -> bool:
    if service.job_store is None:
        return False
    from .jobs import create_agent_promote_request, enqueue_agent_job

    enqueue_agent_job(
        service.job_store,
        create_agent_promote_request(run_id, trigger_id=trigger_id),
        idempotency_key=f"run:{run_id}:promote:{trigger_id}",
    )
    return True


def process_promote_job(service: AgentRunService, run_id: str) -> AgentRunSnapshot:
    """Run acceptance, diff capture and promotion in a durable job."""
    current = service.get(run_id)
    if current is None:
        raise KeyError(run_id)
    if current.status in {"completed", "failed", "cancelled"}:
        return current
    action = None
    with service.unit_of_work(service.database) as work:
        raw_repository = service.repository_factory(work.connection, service.context)
        current = raw_repository.get_run(run_id)
        if current is None:
            raise KeyError(run_id)
        if current.status in {"completed", "failed", "cancelled"}:
            work.rollback()
            return current
        lease = raw_repository.get_active_lease(run_id)
        if lease is None:
            lease = raw_repository.acquire_lease(
                run_id,
                worker_id=service.worker_id,
                ttl_seconds=90,
            )
            service._remember_lease(lease)
        repository = _LeaseBoundRunRepository(
            raw_repository,
            worker_id=lease.worker_id,
            lease_token=lease.lease_token,
        )
        children_terminal, _ = service._children_terminal_state(cast(PostgresAgentRunRepository, repository), run_id)
        if not children_terminal:
            work.rollback()
            raise RuntimeError("agent promotion is waiting for child runs to finish")
        advance_quality = getattr(service, "_advance_quality_on_settle", None)
        if callable(advance_quality):
            action = advance_quality(repository, current)
        else:
            service._finalize_acceptance(cast(PostgresAgentRunRepository, repository), current)
        work.commit()
    execute_action = getattr(service, "_execute_quality_action", None)
    if action and callable(execute_action):
        execute_action(action)
    dispatch_pending = getattr(service, "_dispatch_pending_quality_commands", None)
    if callable(dispatch_pending):
        dispatch_pending(run_id, include_parent=True)
    latest = service.get(run_id)
    if latest is None:
        raise KeyError(run_id)
    if latest.status in {"completed", "failed", "cancelled"}:
        service._close_terminal_runtime(run_id)
    return latest


def _create_child_start(service: AgentRunService, parent_run_id: str, request):
    from .subagents import derive_child_spec

    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        locked = PostgresAgentRunQueries(work.connection, service.context).lock_run(parent_run_id).fetchone()
        if locked is None:
            raise KeyError(parent_run_id)
        parent = repository.get_run(parent_run_id)
        if parent is None:
            raise KeyError(parent_run_id)
        if parent.status in {"completed", "failed", "cancelled"}:
            raise ValueError("cannot start child from terminal parent")
        child_spec = derive_child_spec(parent, request)
        service._validate_run_spec_authority(child_spec)
        service._validate_evidence_authority(child_spec)
        service._reserve_child_start(repository, parent, child_spec)
        snapshot = repository.create_run(child_spec)
        # The grant row references the child run, so it follows create_run.
        service._record_child_grant(repository, parent, child_spec)
        work.commit()
    return child_spec, snapshot


def _reserve_child_start(service: AgentRunService, repository, parent, child_spec: AgentRunSpec) -> None:
    from .subagents import reserve_child_budget

    reserve_child_budget(
        parent,
        repository.list_children(parent.run_id),
        child_spec,
        parent_usage=repository.get_usage(parent.run_id),
    )


def _persist_starting_run(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    issued: AgentRunSpec,
) -> AgentRunSnapshot:
    log_agent_activity(
        "service.start.persisting_run",
        category="service",
        run_id=issued.run_id,
        fields={"worker_id": service.worker_id},
    )
    snapshot = repository.create_run(issued)
    service._capture_workspace_baseline(repository, issued)
    lease = repository.acquire_lease(issued.run_id, worker_id=service.worker_id, ttl_seconds=90)
    service._remember_lease(lease)
    started = repository.update_state(
        issued.run_id,
        expected_revision=snapshot.revision,
        status="starting",
        worker_id=service.worker_id,
        lease_token=lease.lease_token,
    )
    log_agent_activity(
        "service.start.run_persisted",
        category="service",
        run_id=issued.run_id,
        fields={"status": started.status, "revision": started.revision},
    )
    return started


def _launch_runtime(
    service: AgentRunService,
    issued: AgentRunSpec,
    snapshot: AgentRunSnapshot,
    *,
    reference_context: str = "",
    reference_images: list[dict[str, str]] | None = None,
) -> AgentRunSnapshot:
    try:
        log_agent_activity(
            "service.runtime.launch_requested",
            category="service",
            run_id=issued.run_id,
            fields={"status": snapshot.status, "revision": snapshot.revision},
        )
        contextual_start = getattr(service.runtime, "start_with_context", None)
        if (reference_context or reference_images) and callable(contextual_start):
            contextual_start(
                issued,
                reference_context=reference_context,
                **({"reference_images": reference_images} if reference_images else {}),
            )
        else:
            service.runtime.start(issued)
    except Exception as exc:
        log_agent_activity(
            "service.runtime.launch_failed",
            category="service",
            level="error",
            run_id=issued.run_id,
            fields={"status_before_launch": snapshot.status},
            error=exc,
            include_traceback=True,
        )
        service.runtime.close_run(issued.run_id)
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            current = repository.get_run(issued.run_id)
            if current is not None:
                repository.update_state(
                    issued.run_id,
                    expected_revision=current.revision,
                    status="failed",
                    desired_state="cancelled",
                    last_error=f"{type(exc).__name__}: {exc}"[:2000],
                )
            service._maybe_finalize_parent_in_repository(repository, issued.run_id)
            work.commit()
        raise
    result = service.get(issued.run_id) or snapshot
    log_agent_activity(
        "service.runtime.launch_completed",
        category="service",
        run_id=issued.run_id,
        fields={"status": result.status, "revision": result.revision},
    )
    return result
