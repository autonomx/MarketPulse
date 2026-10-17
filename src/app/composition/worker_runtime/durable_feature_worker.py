"""Lease-fenced executor for feature-owned durable jobs."""
from __future__ import annotations

import contextvars
from dataclasses import dataclass
import logging
import threading
import time
import uuid
from typing import Any

from app.jobs.handlers import JobExecutionContext, JobHandlerRegistry, RetryPolicyJobStore
from app.observability.logging import log_context
from app.observability.metrics import record_job_execution
from app.observability.tracing import span
from app.runtime.statement_class import statement_class
from app.jobs.models import CompleteJobRequest, FailJobRequest, JobRecord, JobStatus
from app.persistence.execution_repositories import JobClaimConflict
from app.persistence.identity_service import list_workspace_contexts_with_ready_jobs
from app.runtime.tenant_context import TenantContext, current_tenant, pop_tenant, push_tenant
from app.persistence.unit_of_work import unit_of_work

logger = logging.getLogger(__name__)


class _LeaseBoundJobStore:
    """Bind every mutation to the one job and lease assigned to this attempt."""

    _MUTATING = frozenset(
        {
            "mark_running",
            "update_progress",
            "update_job_input",
            "update_job_stages",
            "complete_job",
            "fail_job",
            "finalize_cancel",
            "append_log",
        }
    )

    def __init__(self, store: Any, job: JobRecord) -> None:
        lease = job.lease
        if lease is None:
            raise JobClaimConflict("Durable feature execution requires a live job lease")
        self._store = store
        self._job_id = job.id
        self._lease_token = lease.token
        self._worker_id = lease.worker_id

    def require_execution_authority(self, job_id: str | None = None) -> None:
        target = job_id or self._job_id
        if target != self._job_id:
            raise JobClaimConflict("Durable feature executor cannot mutate another job")

    def require_execution_authority_in(self, work: Any, job_id: str | None = None) -> None:
        del work
        self.require_execution_authority(job_id)

    def complete_job_in_transaction(
        self,
        work: Any,
        job_id: str,
        request: CompleteJobRequest,
    ):
        self.require_execution_authority_in(work, job_id)
        self._store._append_compat_logs(
            work,
            self._job_id,
            request.logs,
            worker_id=self._worker_id,
            lease_token=self._lease_token,
        )
        record = work.jobs.complete(
            self._store.context,
            job_id=self._job_id,
            worker_id=self._worker_id,
            lease_token=self._lease_token,
            output_refs=request.output_refs,
        )
        record = self._store._hydrate_job_logs(work, record)
        return self._store._record(record)

    def complete_job(self, job_id: str, request: CompleteJobRequest) -> JobRecord | None:
        self.require_execution_authority(job_id)
        return self._store.complete_job(
            job_id,
            request.model_copy(update={
                "worker_id": self._worker_id,
                "lease_token": self._lease_token,
            }),
        )

    def fail_job(self, job_id: str, request: FailJobRequest) -> JobRecord | None:
        self.require_execution_authority(job_id)
        return self._store.fail_job(
            job_id,
            request.model_copy(update={
                "worker_id": self._worker_id,
                "lease_token": self._lease_token,
            }),
        )

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._store, name)
        if name not in self._MUTATING or not callable(value):
            return value

        def guarded(*args: Any, **kwargs: Any) -> Any:
            target = args[0] if args else kwargs.get("job_id")
            self.require_execution_authority(target)
            kwargs["worker_id"] = self._worker_id
            kwargs["lease_token"] = self._lease_token
            return value(*args, **kwargs)

        return guarded


@dataclass(slots=True)
class _ActiveExecution:
    job: JobRecord
    cancellation: threading.Event
    thread: threading.Thread
    context: contextvars.Context | None = None


class DurableFeatureJobWorker:
    """Poll one set of resource classes at an independent concurrency limit."""

    lease_seconds = 30
    renewal_seconds = 10

    def __init__(
        self,
        store: Any,
        registry: JobHandlerRegistry,
        *,
        pool_name: str = "default",
        resource_classes: tuple[str, ...] | list[str] | None = None,
        services: Any = None,
        poll_seconds: float = 0.25,
        max_concurrency: int = 4,
        shutdown_grace_seconds: float = 30,
        worker_id: str | None = None,
    ) -> None:
        if not pool_name or pool_name.strip() != pool_name:
            raise ValueError("pool_name must be a non-empty normalized string")
        if shutdown_grace_seconds < 0:
            raise ValueError("shutdown_grace_seconds cannot be negative")
        self.store = store
        self.registry = registry
        self.pool_name = pool_name
        self.resource_classes = tuple(sorted(set(resource_classes or registry.resource_classes())))
        self.job_types = tuple(sorted(
            spec.type
            for spec in (registry.require(job_type) for job_type in registry.types())
            if spec.resource_class.value in self.resource_classes
        ))
        self.services = services
        if services is not None:
            setattr(store, "runtime_services", services)
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.max_concurrency = max(1, min(int(max_concurrency), 64))
        self.shutdown_grace_seconds = float(shutdown_grace_seconds)
        self.worker_id = worker_id or f"job-worker:{uuid.uuid4().hex}:{pool_name}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_workspace = 0
        self._ready = threading.Event()
        self._active_lock = threading.Lock()
        self._active: dict[str, _ActiveExecution] = {}
        self.claim_count = 0
        self.completed_count = 0
        self.failure_count = 0
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def ready(self) -> bool:
        return self.running and not self._stop.is_set() and self._ready.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"omnix-job-pool-{self.pool_name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, grace_seconds: float | None = None) -> None:
        """Stop claims, drain work, then release any lease still in flight."""
        self._stop.set()
        grace = self.shutdown_grace_seconds if grace_seconds is None else max(0.0, grace_seconds)
        deadline = time.monotonic() + grace
        poller = self._thread
        if poller is not None:
            poller.join(timeout=max(0.0, deadline - time.monotonic()))

        while time.monotonic() < deadline:
            with self._active_lock:
                active = list(self._active.values())
            if not active:
                break
            remaining = deadline - time.monotonic()
            active[0].thread.join(timeout=min(0.1, max(0.0, remaining)))

        with self._active_lock:
            abandoned = list(self._active.values())
        for execution in abandoned:
            execution.cancellation.set()
        for execution in abandoned:
            release_context = execution.context or contextvars.copy_context()
            release_context.copy().run(
                self._release_lease, execution.job, "worker shutdown drain deadline elapsed"
            )
        for execution in abandoned:
            execution.thread.join(timeout=0.2)

    @property
    def active_job_ids(self) -> tuple[str, ...]:
        with self._active_lock:
            return tuple(sorted(self._active))

    def diagnostics(self) -> dict[str, Any]:
        active_job_ids = self.active_job_ids
        return {
            "pool": self.pool_name,
            "ready": self.ready,
            "running": self.running,
            "worker_id": self.worker_id,
            "resource_classes": list(self.resource_classes),
            "job_types": list(self.job_types),
            "max_concurrency": self.max_concurrency,
            "active_job_ids": list(active_job_ids),
            "active_jobs": len(active_job_ids),
            "claim_count": self.claim_count,
            "completed_count": self.completed_count,
            "failure_count": self.failure_count,
            "last_error": self.last_error,
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._reap_finished()
                with self._active_lock:
                    saturated = len(self._active) >= self.max_concurrency
                if not self.job_types:
                    self._ready.set()
                    self._stop.wait(self.poll_seconds)
                    continue
                if saturated:
                    self._stop.wait(self.poll_seconds)
                    continue
                claimed = self._claim_any_workspace()
                self._ready.set()
                if claimed is None:
                    self._stop.wait(self.poll_seconds)
                    continue
                job, job_context = claimed
                if self._stop.is_set():
                    job_context.run(self._release_lease, job, "worker stopping before execution")
                    break
                self._launch_claimed(job, job_context)
            except JobClaimConflict:
                continue
            except Exception as exc:
                self._ready.clear()
                self.failure_count += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("Durable feature job pool iteration failed pool=%s", self.pool_name)
                self._stop.wait(min(2.0, self.poll_seconds * 4))

    def _workspace_contexts(self) -> list[TenantContext]:
        """Active workspaces with a job this worker's pool could claim now.

        One query per poll, with no cap on the number of workspaces. Stores
        without a database (in-memory test doubles) serve the current tenant.
        """
        database = getattr(self.store, "database", None)
        if database is None:
            return [current_tenant()]
        return list_workspace_contexts_with_ready_jobs(database, self.resource_classes)

    def _claim_any_workspace(self) -> tuple[JobRecord, contextvars.Context] | None:
        """Claim the next job in any active workspace, rotating for fairness.

        The returned context carries that workspace's tenant, so execution,
        lease renewal and release all act within the job's own workspace.
        """
        workspaces = self._workspace_contexts()
        if not workspaces:
            return None
        start = self._next_workspace % len(workspaces)
        self._next_workspace += 1
        for workspace in workspaces[start:] + workspaces[:start]:
            token = push_tenant(workspace)
            try:
                job = self._claim_one()
                if job is not None:
                    return job, contextvars.copy_context()
            finally:
                pop_tenant(token)
        return None

    def _launch_claimed(self, job: JobRecord, job_context: contextvars.Context | None = None) -> None:
        cancellation = threading.Event()
        run_context = job_context or contextvars.copy_context()
        execution = threading.Thread(
            target=run_context.run,
            args=(self._run_claimed, job, cancellation),
            name=f"omnix-job-{self.pool_name}-{job.id[-8:]}",
            daemon=True,
        )
        with self._active_lock:
            self._active[job.id] = _ActiveExecution(job, cancellation, execution, run_context)
        execution.start()

    def _run_claimed(self, job: JobRecord, cancellation: threading.Event) -> None:
        try:
            attempt = max(1, int(getattr(job, "_attempt_count", 0) or getattr(job, "attempts", 0) or 1))
            with statement_class("job"), log_context(
                job_id=job.id, attempt=attempt, feature=getattr(job, "module", None),
                request_id=getattr(job, "correlation_id", None),
            ), span(
                "job.execute", job_type=getattr(job, "type", None), job_id=job.id, attempt=attempt,
                pool=self.pool_name, request_id=getattr(job, "correlation_id", None),
            ):
                self._execute_claimed(job, cancellation)
        finally:
            with self._active_lock:
                self._active.pop(job.id, None)

    def _reap_finished(self) -> None:
        with self._active_lock:
            for job_id, execution in tuple(self._active.items()):
                if not execution.thread.is_alive():
                    self._active.pop(job_id, None)

    def _claim_one(self) -> JobRecord | None:
        with unit_of_work(self.store.database) as work:
            record = work.jobs.claim_next(
                self.store.context,
                worker_id=self.worker_id,
                resource_classes=list(self.resource_classes),
                job_types=list(self.job_types),
                lease_seconds=self.lease_seconds,
            )
            if record is None:
                work.rollback()
                return None
            record = work.jobs.mark_running(
                self.store.context,
                job_id=record["id"],
                worker_id=self.worker_id,
                lease_token=record["lease_token"],
            )
            work.commit()
        self.claim_count += 1
        return self.store._record(record)

    def _execute_claimed(self, job: JobRecord, cancellation: threading.Event) -> None:
        lease = job.lease
        if lease is None:
            self._record_unexpected_failure(job, RuntimeError("claimed job has no lease"))
            return
        renewal_stop = threading.Event()
        renewal = threading.Thread(
            # Threads do not inherit context variables: keep the job's tenant.
            target=contextvars.copy_context().run,
            args=(self._renew_loop, job.id, lease.worker_id, lease.token, renewal_stop, cancellation),
            name=f"omnix-job-lease-{job.id[-8:]}",
            daemon=True,
        )
        renewal.start()
        started, outcome = time.perf_counter(), "error"
        try:
            lease_store = _LeaseBoundJobStore(self.store, job)
            result = execute_durable_feature_job(
                lease_store,
                job,
                self.registry,
                services=self.services,
                cancellation=cancellation,
            )
            outcome = str(getattr(result.status, "value", result.status))
            if result.status in {
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELED,
                JobStatus.STALE,
            }:
                self.completed_count += 1
            self.last_error = None
        except JobClaimConflict:
            # An expired or explicitly released token cannot publish a late result.
            outcome = "lease_lost"
            return
        except Exception as exc:
            self.failure_count += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._record_unexpected_failure(job, exc)
        finally:
            renewal_stop.set()
            renewal.join(timeout=1.0)
            record_job_execution(job.type, outcome, time.perf_counter() - started)

    def _renew_loop(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        stop: threading.Event,
        cancellation: threading.Event | None = None,
    ) -> None:
        """Keep the lease alive; tell the handler when it must stop.

        The handler's cancellation is set when the job is asked to cancel
        (``cancel_requested``), when the lease is lost (another worker may own
        the job now), and when renewal keeps failing until the lease would
        have expired. The fenced writes already refuse a late result; the
        signal stops the work itself, so its side effects do not run twice.
        """
        last_renewed = time.monotonic()
        while not stop.wait(self.renewal_seconds):
            try:
                with unit_of_work(self.store.database) as work:
                    renewed = work.jobs.renew_lease(
                        self.store.context,
                        job_id=job_id,
                        worker_id=worker_id,
                        lease_token=lease_token,
                        lease_seconds=self.lease_seconds,
                    )
                    work.commit()
            except JobClaimConflict:
                self._signal_stop(cancellation, job_id, "lease_lost")
                return
            except Exception:
                self._ready.clear()
                if time.monotonic() - last_renewed >= self.lease_seconds:
                    logger.warning("Durable job lease renewal failed until the lease expired job_id=%s",
                                   job_id, exc_info=True)
                    self._signal_stop(cancellation, job_id, "lease_expired")
                    return
                logger.warning("Durable job lease renewal failed; retrying job_id=%s", job_id, exc_info=True)
                continue
            last_renewed = time.monotonic()
            status = renewed.get("status") if isinstance(renewed, dict) else getattr(renewed, "status", None)
            if str(getattr(status, "value", status)) == JobStatus.CANCEL_REQUESTED.value:
                self._signal_stop(cancellation, job_id, "cancel_requested")

    @staticmethod
    def _signal_stop(cancellation: threading.Event | None, job_id: str, reason: str) -> None:
        if cancellation is not None and not cancellation.is_set():
            logger.warning("Durable job told to stop job_id=%s reason=%s", job_id, reason)
            cancellation.set()

    def _release_lease(self, job: JobRecord, reason: str) -> None:
        lease = job.lease
        if lease is None:
            return
        try:
            with unit_of_work(self.store.database) as work:
                work.jobs.release(
                    self.store.context,
                    job_id=job.id,
                    worker_id=lease.worker_id,
                    lease_token=lease.token,
                    reason=reason,
                )
                work.commit()
        except JobClaimConflict:
            # Completion, expiry, or a new claim already consumed this lease.
            return
        except Exception:
            logger.exception("Could not release job lease during shutdown: %s", job.id)

    def _record_unexpected_failure(self, job: JobRecord, exc: Exception) -> None:
        lease = job.lease
        if lease is None:
            return
        try:
            with unit_of_work(self.store.database) as work:
                work.jobs.fail(
                    self.store.context,
                    job_id=job.id,
                    worker_id=lease.worker_id,
                    lease_token=lease.token,
                    error={
                        "code": "durable_feature_execution_failed",
                        "message": type(exc).__name__,
                        "retryable": True,
                        "details": {"job_type": job.type},
                    },
                    retry_delay_seconds=self.registry.retry_delay_seconds(
                        job.type,
                        max(1, int(getattr(job, "_attempt_count", 0) or 1)),
                    ),
                )
                work.commit()
        except JobClaimConflict:
            return
        except Exception:
            logger.exception("Could not persist durable feature worker failure: %s", job.id)


def execute_durable_feature_job(
    job_store: Any,
    job: JobRecord,
    registry: JobHandlerRegistry,
    *,
    services: Any = None,
    cancellation: threading.Event | None = None,
) -> JobRecord:
    """Dispatch an already-leased job through its feature-owned handler."""
    spec = registry.get(job.type)
    if spec is None:
        failed = job_store.fail_job(
            job.id,
            FailJobRequest(
                worker_id=job.lease.worker_id if job.lease is not None else None,
                lease_token=job.lease.token if job.lease is not None else None,
                code="unsupported_job_type",
                message=f"Unsupported durable feature job type: {job.type}",
                retryable=False,
                details={"job_type": job.type},
            ),
        )
        return failed or job
    retry_aware_store = RetryPolicyJobStore(job_store, registry, job)
    return registry.execute(
        JobExecutionContext(
            job_store=retry_aware_store,
            services=services if services is not None else getattr(job_store, "runtime_services", None),
            cancellation=cancellation,
        ),
        job,
    )


__all__ = ["DurableFeatureJobWorker", "execute_durable_feature_job"]
