"""Lease-scoped execution for post-turn memory jobs.

Provider calls run outside transactions. Derived memory and the successful job
result share a transaction guarded by the originally claimed lease.
"""
from __future__ import annotations

import logging
import threading
import uuid
from contextlib import contextmanager

from app.jobs import CompleteJobRequest, FailJobRequest

from app.persistence.execution_repositories import JobClaimConflict
from app.persistence.job_store import PostgresJobStoreAdapter
from app.persistence.transaction_binding import share_transaction
from app.persistence.unit_of_work import unit_of_work
from typing import Any

logger = logging.getLogger(__name__)


class MemoryJobExecution:
    lease_seconds = 30
    renewal_seconds = 10

    def __init__(self, store, job, *, already_claimed: bool = False):
        self.store = store
        self.job = job
        self.already_claimed = already_claimed
        self.claimed = False
        if already_claimed:
            if job.lease is None or not callable(
                getattr(store, "require_execution_authority_in", None)
            ):
                raise RuntimeError(
                    "preclaimed memory job requires an authority-bound lease"
                )
            self.worker_id = job.lease.worker_id
            self.lease_token = job.lease.token
        else:
            self.worker_id = f"memory-post-turn:{uuid.uuid4().hex}"
            self.lease_token = None
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: threading.Thread | None = None
        self._work: Any = None
        self._completed = False

    def __enter__(self):
        if self.already_claimed:
            self.claimed = True
            return self
        if not isinstance(self.store, PostgresJobStoreAdapter):
            self.claimed = True
            return self
        with unit_of_work(self.store.database) as work:
            record = work.jobs.claim_next(
                self.store.context, worker_id=self.worker_id,
                resource_classes=["cpu"], lease_seconds=self.lease_seconds,
                job_id=self.job.id, job_types=["assistant.memory.suggest"],
            )
            if record is not None:
                self.lease_token = record["lease_token"]
                record = work.jobs.mark_running(
                    self.store.context, job_id=record["id"],
                    worker_id=self.worker_id, lease_token=self.lease_token,
                )
            work.commit()
        if record is not None:
            self.job = self.store._record(record)
            self.claimed = True
            self._thread = threading.Thread(
                target=self._renew, name="omnix-memory-lease", daemon=True,
            )
            self._thread.start()
        return self

    def _renew(self):
        while not self._stop.wait(self.renewal_seconds):
            try:
                with unit_of_work(self.store.database) as work:
                    work.jobs.renew_lease(
                        self.store.context, job_id=self.job.id,
                        worker_id=self.worker_id, lease_token=self.lease_token,
                        lease_seconds=self.lease_seconds,
                    )
                    work.commit()
            except Exception:
                # An uncertain renewal is sufficient to fence this execution.
                self._lost.set()
                return

    def _require_lease(self, work):
        if self._lost.is_set():
            raise JobClaimConflict(f"memory job lease lost: {self.job.id}")
        if self.already_claimed:
            self.store.require_execution_authority_in(work, self.job.id)
            return
        work.jobs.require_running_lease(
            self.store.context,
            job_id=self.job.id,
            worker_id=self.worker_id,
            lease_token=self.lease_token,
        )

    @contextmanager
    def write_result(self, memory_service=None):
        if not isinstance(self.store, PostgresJobStoreAdapter) and not self.already_claimed:
            yield
            return
        if memory_service is not None:
            repository = memory_service.repository
            if (getattr(repository, "database", None) is not self.store.database
                    or repository.context.workspace_id != self.store.context.workspace_id):
                raise RuntimeError("Memory writes must share the job database and workspace")
        with unit_of_work(self.store.database) as work:
            self._require_lease(work)
            with share_transaction(work):
                self._work = work
                try:
                    yield
                    if not self._completed:
                        raise RuntimeError("Memory job result was not completed")
                    work.commit()
                except BaseException:
                    self._completed = False
                    raise
                finally:
                    self._work = None

    def complete_job(self, job_id: str, request: CompleteJobRequest):
        if job_id != self.job.id:
            raise ValueError("Memory execution cannot complete another job")
        if self.already_claimed:
            work = self._work
            if work is None:
                raise RuntimeError("Memory completion requires its result transaction")
            self._require_lease(work)
            result = self.store.complete_job_in_transaction(work, job_id, request)
            self._completed = True
            return result
        if not isinstance(self.store, PostgresJobStoreAdapter):
            return self.store.complete_job(job_id, request)
        work = self._work
        if work is None:
            raise RuntimeError("Memory completion requires its result transaction")
        self._require_lease(work)
        self.store._append_compat_logs(
            work,
            job_id,
            request.logs,
            worker_id=self.worker_id,
            lease_token=self.lease_token,
        )
        record = work.jobs.complete(
            self.store.context, job_id=job_id, worker_id=self.worker_id,
            lease_token=self.lease_token, output_refs=request.output_refs,
        )
        record = self.store._hydrate_job_logs(work, record)
        self._completed = True
        return self.store._record(record)

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        if exc is not None and self.already_claimed:
            # The durable feature worker owns failure recording and retry policy.
            return False
        if self.claimed and not self._completed and isinstance(self.store, PostgresJobStoreAdapter):
            try:
                with unit_of_work(self.store.database) as work:
                    record = work.jobs.get_job(self.store.context, self.job.id)
                    if (record is not None and record["status"] == "cancel_requested"
                            and record["lease_owner"] == self.worker_id
                            and record["lease_token"] == self.lease_token):
                        work.jobs.acknowledge_cancel(
                            self.store.context, job_id=self.job.id,
                            worker_id=self.worker_id, lease_token=self.lease_token,
                        )
                        work.commit()
                        return False
                    work.rollback()
            except Exception:
                logger.exception("Could not acknowledge memory job cancellation: %s", self.job.id)
        if exc is not None and self.claimed and not self._completed:
            try:
                if isinstance(self.store, PostgresJobStoreAdapter):
                    with unit_of_work(self.store.database) as work:
                        self._require_lease(work)
                        work.jobs.fail(
                            self.store.context, job_id=self.job.id,
                            worker_id=self.worker_id, lease_token=self.lease_token,
                            error={"code": "memory_suggestion_failed",
                                   "message": type(exc).__name__, "retryable": True},
                        )
                        work.commit()
                else:
                    self.store.fail_job(self.job.id, FailJobRequest(
                        code="memory_suggestion_failed", message=type(exc).__name__,
                        retryable=True,
                    ))
            except JobClaimConflict:
                # Expiry, cancellation or takeover belongs to the durable owner.
                pass
            except Exception:
                logger.exception("Could not record memory job failure: %s", self.job.id)
        return False


__all__ = ["MemoryJobExecution"]
