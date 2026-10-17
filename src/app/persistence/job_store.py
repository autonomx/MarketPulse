from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from collections.abc import Iterator
from typing import Any, TypeVar, cast, overload

from pydantic import BaseModel

from app.jobs.models import (
    CancelJobRequest,
    CancelState,
    ClaimJobRequest,
    ClaimJobResponse,
    CompleteJobRequest,
    CreateJobRequest,
    FailJobRequest,
    JobError,
    JobEventRecord,
    JobLease,
    JobListResponse,
    JobProgress,
    JobRecord,
    ReleaseJobRequest,
    JobStage,
    JobStatus,
)

from app.jobs.handlers import executing_job
from .database import PostgresDatabase, default_database
from .execution_repositories import JobClaimConflict
from app.observability.logging import current_log_context
from app.runtime.pagination import MAX_PAGE_SIZE, decode_cursor, encode_cursor, page_limit
from app.runtime.tenant_context import RequestTenant
from .unit_of_work import run_unit_of_work, unit_of_work


T = TypeVar("T", bound=BaseModel)


def _model(model: type[T], values: dict[str, Any]) -> T:
    fields = getattr(model, "model_fields", {})
    payload = {key: value for key, value in values.items() if key in fields}
    return model.model_validate(payload)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class PostgresJobStoreAdapter:
    """Compatibility facade over the authoritative PostgreSQL job ledger."""
    context = RequestTenant()

    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        context=None,
        chat_execution_owner=None,
        chat_dispatcher=None,
    ) -> None:
        self.database = database or default_database()
        self.context = context
        self.chat_execution_owner = chat_execution_owner
        self.chat_dispatcher = chat_dispatcher
        self.handler_registry = None

    def configure_handler_registry(self, registry: Any) -> None:
        self.handler_registry = registry

    @overload
    def _hydrate_job_logs(self, work: Any, record: dict[str, Any]) -> dict[str, Any]: ...

    @overload
    def _hydrate_job_logs(self, work: Any, record: None) -> None: ...

    def _hydrate_job_logs(self, work: Any, record: dict[str, Any] | None) -> dict[str, Any] | None:
        if record is None:
            return None
        hydrated = dict(record)
        hydrated["logs"] = work.jobs.list_job_logs(self.context, job_id=str(record["id"]))
        return hydrated

    def _record_only_mutation_identity(
        self, job_id: str, worker_id: str | None, lease_token: str | None
    ) -> tuple[str | None, str | None]:
        if bool(worker_id) != bool(lease_token):
            raise JobClaimConflict("job mutation requires both lease credentials")
        if worker_id and lease_token:
            return None, None
        current = self.get_job(job_id)
        if current is None or not self._runs_without_worker_lease(current):
            raise JobClaimConflict(f"job mutation requires caller lease credentials: {job_id}")
        return (
            getattr(self.chat_execution_owner, "node_id", None),
            self._foreground_claim_token(current),
        )

    def _notify_job_observers(self, event: str, record: JobRecord) -> None:
        registry = self.handler_registry
        if registry is not None:
            registry.notify_observers(event, record)

    def create_job(self, request: CreateJobRequest) -> JobRecord:
        return self._create_job(request)

    def create_job_once(self, request: CreateJobRequest, *, idempotency_key: str) -> JobRecord:
        identity = hashlib.sha256(
            f"{self.context.workspace_id}\n{self.context.user_id}\n{request.module}\n{request.type}\n{idempotency_key}".encode("utf-8")
        ).hexdigest()
        return self._create_job(request, job_id=f"job:idempotent:{identity}")

    def _create_job(self, request: CreateJobRequest, *, job_id: str | None = None) -> JobRecord:
        if self.handler_registry is not None:
            request = self.handler_registry.validate_submission(request)
        handler = (
            self.handler_registry.get(request.type)
            if self.handler_registry is not None
            else None
        )
        request_payload = request.model_dump(mode="json")
        stages = request.stages or []
        metadata = dict(request_payload.get("metadata") or {})
        metadata["compat_contract"] = {
            "owner_id": request.owner_id,
            "stages": [stage.model_dump(mode="json") for stage in stages],
            "input_ref": request_payload.get("input_ref"),
            "compat": request_payload.get("compat") or {},
            "cancel": {},
        }
        backoff = handler.retry_backoff if handler is not None else None
        metadata["retry_backoff"] = {
            "base_seconds": float(getattr(backoff, "base_seconds", 2.0)),
            "factor": float(getattr(backoff, "factor", 2.0)),
            "max_seconds": float(getattr(backoff, "max_seconds", 300.0)),
            "jitter": float(getattr(backoff, "jitter", 0.2)),
        }
        with unit_of_work(self.database) as work:
            module = self.handler_registry.owner(request.type) if self.handler_registry is not None else None
            if module is not None:
                # The authoritative lifecycle check, in the job's own transaction (PA-4.3).
                from .module_states import ModuleNotAcceptingWork, read_module_state

                state = read_module_state(work.connection, module)
                if not state.accepts_new_work:
                    running = executing_job()
                    if running is None or self.handler_registry.owner(running[1]) != module or not state.accepts_follow_up():
                        raise ModuleNotAcceptingWork(state)
                    metadata["parent_job_id"] = running[0]
            owner = self.chat_execution_owner
            if request.type == "chat.generate" and owner is not None:
                owner.require_live(work.connection)
                metadata["compat_contract"]["compat"] = {
                    **metadata["compat_contract"]["compat"], "execution_owner": owner.node_id,
                }
            payload = {
                "id": job_id or request_payload.get("id") or self._new_job_id(),
                # JobRecord.owner_id is a logical owner (for example a chat
                # session). PostgreSQL ownership is the trusted principal.
                "owner_user_id": self.context.user_id,
                "module": request.module,
                "job_type": request.type,
                "resource_class": self._enum_value(request.resource_class),
                "priority": request.priority,
                "input_payload": request.input_payload or {},
                "max_attempts": handler.max_attempts if handler is not None else 3,
                "metadata": metadata,
                "correlation_id": current_log_context().get("request_id"),
            }
            if job_id is None:
                record = work.jobs.create_job(self.context, payload)
                created = True
            else:
                record, created = work.jobs.create_job_once(self.context, payload, reconcile_queued=False)
            if created and request.type == 'chat.generate':
                # Admission can wait for another process's session lock. Order
                # accepted jobs by insertion time, not transaction start time.
                record['created_at'] = work.jobs.set_created_at_now(
                    self.context, job_id=record['id']
                ) or record['created_at']
            record["logs"] = work.jobs.list_job_logs(
                self.context, job_id=str(record["id"])
            )
            work.commit()
        created_record = self._record(record)
        if created:
            self._notify_job_observers("created", created_record)
        return created_record

    def list_job_page(
        self,
        *,
        limit: int | None = None,
        status: str | None = None,
        job_types: tuple[str, ...] | None = None,
        modules: tuple[str, ...] | None = None,
        cursor: str | None = None,
    ) -> JobListResponse:
        """One page of jobs, newest first, filtered in SQL (WP-5.5)."""
        size = page_limit(limit, default=100)
        before = decode_cursor(cursor, arity=2)
        with unit_of_work(self.database) as work:
            records = work.jobs.list_jobs(
                self.context,
                limit=size + 1,
                status=status,
                job_types=job_types,
                modules=modules,
                before_created_at=before[0] if before else None,
                before_id=before[1] if before else None,
            )
            page = records[:size]
            logs_by_job = work.jobs.list_job_logs_for_jobs(
                self.context, job_ids=[str(record["id"]) for record in page]
            )
            for record in page:
                record["logs"] = logs_by_job.get(str(record["id"]), [])
            work.rollback()
        has_more = len(records) > size
        return JobListResponse(
            jobs=[self._record(record) for record in page],
            next_cursor=encode_cursor(page[-1]["created_at"], page[-1]["id"]) if has_more else None,
            has_more=has_more,
        )

    def list_jobs(
        self,
        limit: int | None = None,
        *,
        status: str | None = None,
        job_types: tuple[str, ...] | None = None,
        modules: tuple[str, ...] | None = None,
    ) -> list[JobRecord]:
        """The newest ``limit`` matching jobs (at most one page)."""
        return self.list_job_page(limit=limit, status=status, job_types=job_types, modules=modules).jobs

    def iter_jobs(
        self,
        *,
        status: str | None = None,
        job_types: tuple[str, ...] | None = None,
        modules: tuple[str, ...] | None = None,
    ) -> Iterator[JobRecord]:
        """Every matching job, newest first, one page at a time."""
        cursor: str | None = None
        while True:
            page = self.list_job_page(
                limit=MAX_PAGE_SIZE, status=status, job_types=job_types, modules=modules, cursor=cursor
            )
            yield from page.jobs
            if not page.has_more or not page.next_cursor:
                return
            cursor = page.next_cursor

    def get_job(self, job_id: str) -> JobRecord | None:
        with unit_of_work(self.database) as work:
            record = work.jobs.get_job(self.context, job_id)
            record = self._hydrate_job_logs(work, record)
            work.rollback()
        return self._record(record) if record is not None else None

    def delete_job(self, job_id: str) -> bool:
        with unit_of_work(self.database) as work:
            deleted = work.jobs.delete_job(self.context, job_id=job_id)
            work.commit()
        return deleted

    def claim_next(
        self,
        request: ClaimJobRequest,
        *,
        residency: list[Any] | None = None,
        residency_policy: Any | None = None,
    ) -> ClaimJobResponse:
        del residency, residency_policy
        worker_id = str(
            getattr(request, "worker_id", None)
            or getattr(request, "owner_id", None)
            or "worker:local"
        )
        resource_classes = [self._enum_value(value) for value in request.resource_classes]
        lease_seconds = int(
            getattr(request, "lease_seconds", None)
            or getattr(request, "lease_duration_seconds", None)
            or 30
        )
        def claim(work: Any) -> Any:
            claimed = work.jobs.claim_next(
                self.context,
                worker_id=worker_id,
                resource_classes=resource_classes,
                lease_seconds=lease_seconds,
            )
            return self._hydrate_job_logs(work, claimed)

        # Claims race other workers; a deadlock or serialization failure is retried.
        record = run_unit_of_work(self.database, claim)
        if record is None:
            return ClaimJobResponse(ok=False, reason="no_runnable_job")
        return ClaimJobResponse(ok=True, job=self._record(record))

    def renew_lease(
        self,
        job_id: str,
        *,
        worker_id: str | None = None,
        lease_token: str | None = None,
        lease_seconds: int = 30,
    ) -> JobRecord | None:
        if not worker_id or not lease_token:
            raise JobClaimConflict(f"job has no active lease: {job_id}")
        with unit_of_work(self.database) as work:
            work.jobs.renew_lease(
                self.context,
                job_id=job_id,
                worker_id=worker_id,
                lease_token=lease_token,
                lease_seconds=lease_seconds,
            )
            work.commit()
        return self.get_job(job_id)

    def mark_running(
        self,
        job_id: str,
        *,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> JobRecord | None:
        if bool(worker_id) != bool(lease_token):
            raise JobClaimConflict("mark_running requires both lease credentials")
        if not worker_id:
            current = self.get_job(job_id)
            if current is None:
                return None
            if not self._runs_without_worker_lease(current):
                raise JobClaimConflict(f"job start requires caller lease credentials: {job_id}")
            with unit_of_work(self.database) as work:
                record = work.jobs.mark_record_only_running(
                    self.context, job_id=job_id,
                    execution_owner=getattr(self.chat_execution_owner, "node_id", None),
                    submission_claim_token=self._foreground_claim_token(current),
                )
                record = self._hydrate_job_logs(work, record)
                work.commit()
            started = self._record(record)
            self._notify_job_observers("started", started)
            return started
        with unit_of_work(self.database) as work:
            record = work.jobs.mark_running(
                self.context,
                job_id=job_id,
                worker_id=worker_id,
                # Both credentials are present: checked together above.
                lease_token=cast(str, lease_token),
            )
            record = self._hydrate_job_logs(work, record)
            work.commit()
        started = self._record(record)
        self._notify_job_observers("started", started)
        return started

    def complete_job(self, job_id: str, request: CompleteJobRequest) -> JobRecord | None:
        owner = request.worker_id
        token = request.lease_token
        if bool(owner) != bool(token):
            raise JobClaimConflict(f"job completion requires caller lease credentials: {job_id}")
        request_payload = request.model_dump(mode="json")
        if owner and token:
            execution_owner = submission_token = None
        else:
            current = self.get_job(job_id)
            if current is None:
                return None
            if not self._runs_without_worker_lease(current):
                raise JobClaimConflict(f"job completion requires an active lease: {job_id}")
            execution_owner, submission_token = self._record_only_mutation_identity(
                job_id, None, None
            )
        with unit_of_work(self.database) as work:
            if owner and token:
                self._append_compat_logs(
                    work, job_id, request_payload.get("logs") or [],
                    worker_id=owner, lease_token=token,
                )
                record = work.jobs.complete(
                    self.context,
                    job_id=job_id,
                    worker_id=owner,
                    lease_token=token,
                    output_refs=request.output_refs,
                    progress={"current": 1, "total": 1, "message": "completed"},
                )
            else:
                self._append_compat_logs(
                    work, job_id, request_payload.get("logs") or [],
                    execution_owner=execution_owner,
                    submission_claim_token=submission_token,
                )
                record = work.jobs.complete_record_only(
                    self.context,
                    job_id=job_id,
                    output_refs=request.output_refs,
                    progress={"current": 1, "total": 1, "message": "completed"},
                    execution_owner=execution_owner,
                    submission_claim_token=submission_token,
                )
            record = self._hydrate_job_logs(work, record)
            work.commit()
        completed = self._record(record)
        self._notify_job_observers("completed", completed)
        return completed

    def fail_job(self, job_id: str, request: FailJobRequest) -> JobRecord | None:
        owner = request.worker_id
        token = request.lease_token
        if bool(owner) != bool(token):
            raise JobClaimConflict(f"job failure requires caller lease credentials: {job_id}")
        payload = request.model_dump(mode="json")
        error = payload.get("error") or {
            "code": payload.get("code") or "job_failed",
            "message": payload.get("message") or "job failed",
            "retryable": bool(payload.get("retryable", False)),
        }
        if owner and token:
            execution_owner = submission_token = None
        else:
            current = self.get_job(job_id)
            if current is None:
                return None
            if not self._runs_without_worker_lease(current):
                raise JobClaimConflict(f"job failure requires an active lease: {job_id}")
            execution_owner, submission_token = self._record_only_mutation_identity(
                job_id, None, None
            )
        with unit_of_work(self.database) as work:
            if owner and token:
                self._append_compat_logs(
                    work, job_id, payload.get("logs") or [],
                    worker_id=owner, lease_token=token,
                )
                record = work.jobs.fail(
                    self.context,
                    job_id=job_id,
                    worker_id=owner,
                    lease_token=token,
                    error=error,
                    retry_delay_seconds=int(getattr(request, "_retry_delay_seconds", 0) or 0),
                )
            else:
                self._append_compat_logs(
                    work, job_id, payload.get("logs") or [],
                    execution_owner=execution_owner,
                    submission_claim_token=submission_token,
                )
                record = work.jobs.fail_record_only(
                    self.context,
                    job_id=job_id,
                    error=error,
                    execution_owner=execution_owner,
                    submission_claim_token=submission_token,
                )
            record = self._hydrate_job_logs(work, record)
            work.commit()
        failed = self._record(record)
        self._notify_job_observers("failed", failed)
        return failed

    def release_job(self, job_id: str, request: ReleaseJobRequest) -> JobRecord | None:
        current = self.get_job(job_id)
        if current is None:
            return None
        if not request.worker_id or not request.lease_token:
            raise JobClaimConflict(f"job release requires caller lease credentials: {job_id}")
        with unit_of_work(self.database) as work:
            record = work.jobs.release(
                self.context,
                job_id=job_id,
                worker_id=request.worker_id,
                lease_token=request.lease_token,
                reason=request.reason,
            )
            work.commit()
        return self._record(record)

    def request_cancel(self, job_id: str, request: CancelJobRequest) -> JobRecord | None:
        with unit_of_work(self.database) as work:
            record = work.jobs.get_job(self.context, job_id)
            if record is None:
                work.rollback()
                return None
            work.jobs.request_cancel(self.context, job_id)
            payload = request.model_dump(mode="json")
            work.jobs.set_cancel_compat(self.context, job_id=job_id, payload=payload)
            updated = work.jobs.get_job(self.context, job_id)
            updated = self._hydrate_job_logs(work, updated)
            work.commit()
        return self._record(updated) if updated is not None else None

    def update_progress(
        self,
        job_id: str,
        progress: JobProgress,
        *,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> JobRecord | None:
        execution_owner, submission_token = self._record_only_mutation_identity(
            job_id, worker_id, lease_token
        )
        with unit_of_work(self.database) as work:
            updated = work.jobs.update_progress_compat(
                self.context,
                job_id=job_id,
                progress=progress.model_dump(mode="json"),
                worker_id=worker_id,
                lease_token=lease_token,
                execution_owner=execution_owner,
                submission_claim_token=submission_token,
            )
            work.commit()
        return self.get_job(job_id) if updated else None

    def update_job_input(
        self,
        job_id: str,
        input_payload: dict[str, Any],
        *,
        compat: dict[str, Any] | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> JobRecord | None:
        execution_owner, submission_token = self._record_only_mutation_identity(
            job_id, worker_id, lease_token
        )
        with unit_of_work(self.database) as work:
            updated = work.jobs.update_input_compat(
                self.context,
                job_id=job_id,
                input_payload=input_payload,
                compat=compat,
                worker_id=worker_id,
                lease_token=lease_token,
                execution_owner=execution_owner,
                submission_claim_token=submission_token,
            )
            work.commit()
        return self.get_job(job_id) if updated else None

    def update_awaiting_plan_input(
        self,
        job_id: str,
        input_payload: dict[str, Any],
        *,
        compat: dict[str, Any] | None = None,
    ) -> JobRecord | None:
        with unit_of_work(self.database) as work:
            updated = work.jobs.update_awaiting_plan_input(
                self.context,
                job_id=job_id,
                input_payload=input_payload,
                compat=compat,
            )
            record = self._hydrate_job_logs(
                work, work.jobs.get_job(self.context, job_id)
            ) if updated else None
            work.commit()
        return self._record(record) if record is not None else None

    def update_job_stages(
        self,
        job_id: str,
        stages: list[JobStage],
        *,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> JobRecord | None:
        execution_owner, submission_token = self._record_only_mutation_identity(
            job_id, worker_id, lease_token
        )
        with unit_of_work(self.database) as work:
            updated = work.jobs.update_stages_compat(
                self.context,
                job_id=job_id,
                stages=[stage.model_dump(mode="json") for stage in stages],
                worker_id=worker_id,
                lease_token=lease_token,
                execution_owner=execution_owner,
                submission_claim_token=submission_token,
            )
            work.commit()
        return self.get_job(job_id) if updated else None

    def finalize_cancel(
        self,
        job_id: str,
        reason: str,
        *,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> JobRecord | None:
        execution_owner, submission_token = self._record_only_mutation_identity(
            job_id, worker_id, lease_token
        )
        now = _utcnow()
        with unit_of_work(self.database) as work:
            updated = work.jobs.finalize_cancel_compat(
                self.context, job_id=job_id, reason=reason, now=now,
                worker_id=worker_id,
                lease_token=lease_token,
                execution_owner=execution_owner,
                submission_claim_token=submission_token,
            )
            work.commit()
        return self.get_job(job_id) if updated else None

    def append_log(
        self,
        job_id: str,
        message: str,
        *,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> JobRecord | None:
        execution_owner, submission_token = self._record_only_mutation_identity(
            job_id, worker_id, lease_token
        )
        with unit_of_work(self.database) as work:
            self._append_compat_logs(
                work,
                job_id,
                [str(message)],
                worker_id=worker_id,
                lease_token=lease_token,
                execution_owner=execution_owner,
                submission_claim_token=submission_token,
            )
            work.commit()
        return self.get_job(job_id)

    def list_events(self, job_id: str) -> list[JobEventRecord]:
        with unit_of_work(self.database) as work:
            rows = work.jobs.list_job_events(self.context, job_id=job_id)
            work.rollback()
        return [
            _model(
                JobEventRecord,
                {
                    "id": int(row[0]),
                    "job_id": str(row[1]),
                    "event_type": str(row[2]),
                    "type": str(row[2]),
                    "payload": dict(row[3]),
                    "created_at": row[4].isoformat(),
                },
            )
            for row in rows
        ]

    @staticmethod
    def _new_job_id() -> str:
        import uuid

        return f"job:{uuid.uuid4().hex}"

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(getattr(value, "value", value))

    @staticmethod
    def _lease_value(lease: Any, *names: str) -> str | None:
        if lease is None:
            return None
        for name in names:
            value = getattr(lease, name, None)
            if value:
                return str(value)
        return None

    @staticmethod
    def _runs_without_worker_lease(job: JobRecord) -> bool:
        return (
            job.type == "chat.generate" and job.compat.get("inline_execution") is True
        ) or (
            job.module == "rpg" and job.type == "rpg.turn.foreground_record"
            and job.compat.get("record_only") is True
        )

    def _foreground_claim_token(self, job: JobRecord) -> str | None:
        from app.jobs.foreground_execution import current_foreground_execution

        execution = current_foreground_execution()
        if execution is None:
            return None
        if (execution.workspace_id != self.context.workspace_id or execution.job_id != job.id
                or execution.session_id != (job.input_ref or {}).get("session_id")
                or execution.submission_id != (job.input_payload or {}).get("submission_id")):
            raise JobClaimConflict("foreground execution belongs to another job")
        return execution.claim_token

    @staticmethod
    def _json(value: Any) -> str:
        import json

        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _append_compat_logs(
        self,
        work: Any,
        job_id: str,
        logs: list[Any],
        *,
        worker_id: str | None = None,
        lease_token: str | None = None,
        execution_owner: str | None = None,
        submission_claim_token: str | None = None,
    ) -> None:
        if not logs:
            return
        appended = work.jobs.append_compat_logs(
            self.context,
            job_id=job_id,
            logs=[self._compat_log(item) for item in logs],
            worker_id=worker_id,
            lease_token=lease_token,
            execution_owner=execution_owner,
            submission_claim_token=submission_claim_token,
        )
        if not appended:
            raise JobClaimConflict(f"job log append rejected: {job_id}")

    @staticmethod
    def _compat_log(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {"level": "info", "message": str(value)}

    def _record(self, value: dict[str, Any]) -> JobRecord:
        metadata = dict(value.get("metadata") or {})
        contract = dict(metadata.get("compat_contract") or {})
        stages = [JobStage.model_validate(item) for item in contract.get("stages") or []]
        progress = JobProgress.model_validate(value.get("progress") or {})
        error_value = value.get("error")
        error = None
        if error_value:
            error_payload = (
                dict(error_value)
                if isinstance(error_value, dict)
                else {"code": "job_failed", "message": str(error_value)}
            )
            error_payload.setdefault("code", "job_failed")
            error_payload.setdefault(
                "message",
                str(
                    error_payload.get("detail")
                    or error_payload.get("code")
                    or "Job failed"
                ),
            )
            error = JobError.model_validate(error_payload)
        lease = None
        if value.get("lease_token"):
            lease = _model(
                JobLease,
                {
                    "worker_id": value.get("lease_owner"),
                    "owner_id": value.get("lease_owner"),
                    "lease_token": value.get("lease_token"),
                    "token": value.get("lease_token"),
                    "claimed_at": (
                        value.get("started_at")
                        or value.get("updated_at")
                        or value.get("created_at")
                    ),
                    "expires_at": value.get("lease_expires_at"),
                },
            )
        cancel = _model(CancelState, dict(contract.get("cancel") or {}))
        resource_class = value["resource_class"]
        status = "canceled" if value["status"] == "cancelled" else value["status"]
        record = _model(
            JobRecord,
            {
                "id": value["id"],
                "owner_id": contract.get("owner_id") or value.get("owner_user_id"),
                "module": value["module"],
                "type": value["job_type"],
                "job_type": value["job_type"],
                "status": JobStatus(status),
                "resource_class": resource_class,
                "priority": value["priority"],
                "stages": stages,
                "progress": progress,
                "logs": [self._compat_log(item) for item in value.get("logs") or []],
                "input_ref": contract.get("input_ref"),
                "input_payload": value.get("input_payload") or {},
                "output_refs": value.get("output_refs") or [],
                "error": error,
                "lease": lease,
                "created_at": value["created_at"],
                "updated_at": value["updated_at"],
                "started_at": value.get("started_at"),
                "completed_at": value.get("completed_at"),
                "cancel": cancel,
                "compat": dict(contract.get("compat") or {}),
                "correlation_id": value.get("correlation_id"),
            },
        )
        record._attempt_count = max(0, int(value.get("attempt_count") or 0))
        return record
