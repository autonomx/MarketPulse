"""Provider-free in-memory test double for the shared job contract."""

from __future__ import annotations

import hashlib
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.jobs.errors import JobClaimConflict
from app.observability.logging import current_log_context
from app.runtime.pagination import decode_cursor, encode_cursor, page_limit
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
    JobProgress,
    JobListResponse,
    JobRecord,
    ReleaseJobRequest,
    JobStage,
    JobStatus,
    ResourceClass,
    TERMINAL_STATUSES,
)

_RUNNABLE = {JobStatus.QUEUED, JobStatus.RETRYING, JobStatus.WAITING}
_ACTIVE = {JobStatus.LEASED, JobStatus.RUNNING, JobStatus.CANCEL_REQUESTED}


def _awaiting_plan_approval(job: JobRecord) -> bool:
    return (
        job.type == "assistant.deep_research"
        and isinstance(job.input_payload, dict)
        and job.input_payload.get("awaiting_plan_approval") is True
    )


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_stage(request: CreateJobRequest) -> JobStage:
    return JobStage(id="run", label=request.type, resource_class=request.resource_class)


@dataclass
class _State:
    lock: threading.RLock = field(default_factory=threading.RLock)
    jobs: dict[str, JobRecord] = field(default_factory=dict)
    events: list[JobEventRecord] = field(default_factory=list)
    next_event_id: int = 1


_STATES: dict[str, _State] = {}
_STATES_LOCK = threading.RLock()


def _state_key(path: str | Path | None) -> str:
    return str(path or ":memory:default")


def _state(path: str | Path | None) -> _State:
    key = _state_key(path)
    with _STATES_LOCK:
        return _STATES.setdefault(key, _State())


class InMemoryJobStore:
    """Same public API as the retired local store, with no database dependency."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else Path(":memory:")
        self._state = _state(db_path)

    def create_job(self, request: CreateJobRequest) -> JobRecord:
        return self._create_job(request, f"job:{uuid.uuid4().hex}")

    def create_job_once(
        self, request: CreateJobRequest, *, idempotency_key: str
    ) -> JobRecord:
        identity = hashlib.sha256(
            f"{request.module}\n{request.type}\n{idempotency_key}".encode("utf-8")
        ).hexdigest()
        return self._create_job(request, f"job:idempotent:{identity}")

    def _create_job(self, request: CreateJobRequest, job_id: str) -> JobRecord:
        now = _utcnow()
        job = JobRecord(
            id=job_id,
            owner_id=request.owner_id,
            module=request.module,
            type=request.type,
            status=JobStatus.QUEUED,
            resource_class=request.resource_class,
            priority=request.priority,
            stages=request.stages or [_default_stage(request)],
            progress=JobProgress(),
            logs=[],
            input_ref=request.input_ref,
            input_payload=request.input_payload,
            output_refs=[],
            error=None,
            lease=None,
            created_at=now,
            updated_at=now,
            cancel=CancelState(),
            compat=request.compat,
            correlation_id=current_log_context().get("request_id"),
        )
        with self._state.lock:
            existing = self._state.jobs.get(job.id)
            if existing is not None:
                return deepcopy(existing)
            self._state.jobs[job.id] = deepcopy(job)
            self._event(job.id, "job.created", job.model_dump(mode="json"))
        return deepcopy(job)

    def _matching(self, status=None, job_types=None, modules=None) -> list[JobRecord]:
        jobs = sorted(
            self._state.jobs.values(),
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )
        return [
            job
            for job in jobs
            if (status is None or str(getattr(job.status, "value", job.status)) == status)
            and (not job_types or job.type in job_types)
            and (not modules or job.module in modules)
        ]

    def list_jobs(self, limit: int | None = None, *, status=None, job_types=None, modules=None) -> list[JobRecord]:
        with self._state.lock:
            jobs = self._matching(status, job_types, modules)
            if limit is not None:
                jobs = jobs[: max(0, int(limit))]
            return deepcopy(jobs)

    def list_job_page(self, *, limit=None, status=None, job_types=None, modules=None, cursor=None) -> JobListResponse:
        size = page_limit(limit, default=100)
        before = decode_cursor(cursor, arity=2)
        with self._state.lock:
            jobs = self._matching(status, job_types, modules)
            if before is not None:
                jobs = [job for job in jobs if (str(job.created_at), job.id) < (before[0], before[1])]
            page = deepcopy(jobs[:size])
        has_more = len(jobs) > size
        return JobListResponse(
            jobs=page,
            next_cursor=encode_cursor(str(page[-1].created_at), page[-1].id) if has_more else None,
            has_more=has_more,
        )

    def iter_jobs(self, *, status=None, job_types=None, modules=None):
        with self._state.lock:
            jobs = deepcopy(self._matching(status, job_types, modules))
        yield from jobs

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._state.lock:
            job = self._state.jobs.get(job_id)
            return deepcopy(job) if job is not None else None

    def find_job_by_submission(
        self,
        *,
        job_type: str,
        session_id: str,
        submission_id: str,
    ) -> JobRecord | None:
        with self._state.lock:
            matches = [
                job
                for job in self._state.jobs.values()
                if job.type == job_type
                and isinstance(job.input_ref, dict)
                and job.input_ref.get("session_id") == session_id
                and isinstance(job.input_payload, dict)
                and job.input_payload.get("submission_id") == submission_id
            ]
            matches.sort(key=lambda item: (item.created_at, item.id), reverse=True)
            return deepcopy(matches[0]) if matches else None

    def delete_job(self, job_id: str) -> bool:
        with self._state.lock:
            existed = self._state.jobs.pop(job_id, None) is not None
            if existed:
                self._state.events = [event for event in self._state.events if event.job_id != job_id]
            return existed

    def claim_next(
        self,
        request: ClaimJobRequest,
        *,
        residency: list[Any] | None = None,
        residency_policy: Any | None = None,
    ) -> ClaimJobResponse:
        from app.jobs.residency import (
            ResidencyDecisionAction,
            gpu_residency_request_from_job,
            plan_model_residency,
        )

        now = datetime.now(timezone.utc)
        allowed = {resource.value for resource in request.resource_classes}
        with self._state.lock:
            self._release_expired(now)
            active = [job for job in self._state.jobs.values() if job.status in _ACTIVE]
            active_gpu = any(job.resource_class.value.startswith("gpu:") for job in active)
            active_cpu = sum(1 for job in active if job.resource_class == ResourceClass.CPU)
            # Break created_at ties by insertion order (FIFO); coarse clocks on
            # Windows give back-to-back jobs equal timestamps.
            insertion = {job_id: index for index, job_id in enumerate(self._state.jobs)}
            candidates = sorted(
                (
                    job
                    for job in self._state.jobs.values()
                    if job.status in _RUNNABLE and not _awaiting_plan_approval(job)
                ),
                key=lambda item: (-item.priority, item.created_at, insertion[item.id]),
            )
            for job in candidates:
                if allowed and job.resource_class.value not in allowed:
                    continue
                decision = None
                if residency is not None:
                    residency_request = gpu_residency_request_from_job(job)
                    if residency_request is not None:
                        decision = plan_model_residency(
                            residency_request,
                            residency,
                            residency_policy,
                        )
                        if decision.action != ResidencyDecisionAction.CAN_RUN:
                            continue
                can_share_gpu = bool(
                    decision is not None
                    and residency_policy is not None
                    and getattr(residency_policy, "allow_co_residency", False)
                )
                if job.resource_class.value.startswith("gpu:") and active_gpu and not can_share_gpu:
                    continue
                if job.resource_class == ResourceClass.CPU and active_cpu >= request.cpu_limit:
                    continue
                claimed_at = now.isoformat()
                claimed = deepcopy(job)
                claimed.status = JobStatus.LEASED
                claimed.lease = JobLease(
                    worker_id=request.worker_id,
                    token=uuid.uuid4().hex,
                    claimed_at=claimed_at,
                    expires_at=(now + timedelta(seconds=request.lease_seconds)).isoformat(),
                )
                claimed.updated_at = claimed_at
                claimed.started_at = claimed.started_at or claimed_at
                self._save(claimed, "job.updated")
                return ClaimJobResponse(ok=True, job=deepcopy(claimed))
        return ClaimJobResponse(ok=False, reason="no_runnable_job")

    def mark_running(
        self,
        job_id: str,
        *,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> JobRecord | None:
        if bool(worker_id) != bool(lease_token):
            raise JobClaimConflict("mark_running requires both lease credentials")
        with self._state.lock:
            job = self._state.jobs.get(job_id)
            if job is None:
                return None
            # Mirror the PostgreSQL fence: the caller's claim must own the lease.
            if worker_id and job.lease is not None and (
                job.lease.worker_id != worker_id or job.lease.token != lease_token
            ):
                raise JobClaimConflict(f"stale lease for job {job_id}")
            if job.status not in _RUNNABLE | {JobStatus.LEASED}:
                return deepcopy(job)
            now = _utcnow()
            value = deepcopy(job)
            value.status = JobStatus.RUNNING
            value.updated_at = now
            value.started_at = value.started_at or now
            if value.stages:
                stage = value.stages[0]
                value.stages[0] = stage.model_copy(
                    update={
                        "status": JobStatus.RUNNING,
                        "started_at": stage.started_at or now,
                        "progress": JobProgress(current=0, total=1, message="running"),
                    }
                )
            self._save(value, "job.updated")
            return deepcopy(value)

    def update_progress(
        self,
        job_id: str,
        *,
        current: int,
        total: int,
        message: str | None = None,
        stage_id: str | None = None,
        stage_status: JobStatus = JobStatus.RUNNING,
    ) -> JobRecord | None:
        with self._state.lock:
            job = self._state.jobs.get(job_id)
            if job is None or job.status in TERMINAL_STATUSES:
                return deepcopy(job) if job is not None else None
            value = deepcopy(job)
            now = _utcnow()
            progress = JobProgress(current=max(0, current), total=max(1, total), message=message)
            value.progress = progress
            value.updated_at = now
            if value.status in _RUNNABLE | {JobStatus.LEASED}:
                value.status = JobStatus.RUNNING
                value.started_at = value.started_at or now
            if stage_id:
                value.stages = [
                    stage.model_copy(
                        update={
                            "status": stage_status,
                            "started_at": stage.started_at or now,
                            "completed_at": now if stage_status == JobStatus.COMPLETED else stage.completed_at,
                            "progress": JobProgress(
                                current=1 if stage_status == JobStatus.COMPLETED else 0,
                                total=1,
                                message=message,
                            ),
                        }
                    )
                    if stage.id == stage_id
                    else stage
                    for stage in value.stages
                ]
            self._save(value, "job.updated")
            return deepcopy(value)

    def update_job_input(
        self,
        job_id: str,
        input_payload: dict[str, Any],
        *,
        compat: dict[str, Any] | None = None,
    ) -> JobRecord | None:
        with self._state.lock:
            job = self._state.jobs.get(job_id)
            if job is None or job.status in TERMINAL_STATUSES:
                return deepcopy(job) if job is not None else None
            value = deepcopy(job)
            value.input_payload = deepcopy(input_payload)
            if compat is not None:
                value.compat = deepcopy(compat)
            value.updated_at = _utcnow()
            self._save(value, "job.updated")
            return deepcopy(value)

    def update_job_stages(self, job_id: str, stages: list[JobStage]) -> JobRecord | None:
        with self._state.lock:
            job = self._state.jobs.get(job_id)
            if job is None or job.status in TERMINAL_STATUSES:
                return deepcopy(job) if job is not None else None
            value = deepcopy(job)
            value.stages = deepcopy(stages)
            value.updated_at = _utcnow()
            self._save(value, "job.updated")
            return deepcopy(value)

    def finalize_cancel(self, job_id: str, reason: str) -> JobRecord | None:
        with self._state.lock:
            job = self._state.jobs.get(job_id)
            if job is None:
                return None
            if job.status in TERMINAL_STATUSES:
                return deepcopy(job)
            value = deepcopy(job)
            now = _utcnow()
            value.status = JobStatus.CANCELED
            value.updated_at = now
            value.completed_at = now
            value.lease = None
            value.cancel = CancelState(
                requested=True,
                requested_at=value.cancel.requested_at or now,
                acknowledged_at=now,
                reason=value.cancel.reason or reason,
            )
            value.progress = JobProgress(
                current=value.progress.current,
                total=value.progress.total,
                message="canceled",
            )
            value.stages = [
                stage.model_copy(
                    update={
                        "status": JobStatus.CANCELED if stage.status == JobStatus.RUNNING else stage.status,
                        "completed_at": now if stage.status == JobStatus.RUNNING else stage.completed_at,
                    }
                )
                for stage in value.stages
            ]
            self._save(value, "job.canceled")
            return deepcopy(value)

    def complete_job(self, job_id: str, request: CompleteJobRequest) -> JobRecord | None:
        with self._state.lock:
            job = self._state.jobs.get(job_id)
            if job is None:
                return None
            if job.status in TERMINAL_STATUSES:
                return deepcopy(job)
            value = deepcopy(job)
            now = _utcnow()
            value.status = JobStatus.COMPLETED
            value.updated_at = now
            value.completed_at = now
            value.lease = None
            value.output_refs = request.output_refs
            value.logs.extend(request.logs)
            value.progress = JobProgress(current=1, total=1, message="completed")
            value.stages = [
                stage.model_copy(
                    update={
                        "status": JobStatus.COMPLETED,
                        "completed_at": stage.completed_at or now,
                        "progress": JobProgress(current=1, total=1, message="completed"),
                    }
                )
                for stage in value.stages
            ]
            self._save(value, "job.completed")
            return deepcopy(value)

    def fail_job(self, job_id: str, request: FailJobRequest) -> JobRecord | None:
        with self._state.lock:
            job = self._state.jobs.get(job_id)
            if job is None:
                return None
            if job.status in TERMINAL_STATUSES:
                return deepcopy(job)
            value = deepcopy(job)
            now = _utcnow()
            value.status = JobStatus.FAILED
            value.updated_at = now
            value.completed_at = now
            value.lease = None
            value.error = JobError(
                code=request.code,
                message=request.message,
                retryable=request.retryable,
                details=request.details,
            )
            value.stages = [
                stage.model_copy(
                    update={
                        "status": JobStatus.FAILED,
                        "completed_at": stage.completed_at or now,
                        "error": value.error,
                    }
                )
                if stage.status in {JobStatus.RUNNING, JobStatus.LEASED}
                else stage
                for stage in value.stages
            ]
            self._save(value, "job.failed")
            return deepcopy(value)

    def release_job(self, job_id: str, request: ReleaseJobRequest) -> JobRecord | None:
        with self._state.lock:
            job = self._state.jobs.get(job_id)
            if (
                job is None
                or job.status not in {JobStatus.LEASED, JobStatus.RUNNING}
                or job.lease is None
                or job.lease.worker_id != request.worker_id
                or job.lease.token != request.lease_token
            ):
                return None
            now = _utcnow()
            value = deepcopy(job)
            value.status = JobStatus.QUEUED
            value.updated_at = now
            value.completed_at = None
            value.lease = None
            value.error = None
            self._save(value, "job.released")
            return deepcopy(value)

    def cancel_job(self, job_id: str, request: CancelJobRequest) -> JobRecord | None:
        with self._state.lock:
            job = self._state.jobs.get(job_id)
            if job is None:
                return None
            if job.status in TERMINAL_STATUSES:
                return deepcopy(job)
            value = deepcopy(job)
            now = _utcnow()
            value.cancel = CancelState(
                requested=True,
                requested_at=now,
                acknowledged_at=now if value.status in _RUNNABLE else None,
                reason=request.reason,
            )
            if value.status in _RUNNABLE:
                value.status = JobStatus.CANCELED
                value.completed_at = now
                event_type = "job.canceled"
            else:
                value.status = JobStatus.CANCEL_REQUESTED
                event_type = "job.updated"
            value.updated_at = now
            self._save(value, event_type)
            return deepcopy(value)

    def list_events(self, after_id: int = 0, limit: int = 100) -> list[JobEventRecord]:
        with self._state.lock:
            return deepcopy(
                [event for event in self._state.events if int(event.id) > int(after_id)][: max(0, int(limit))]
            )

    def append_log(self, job_id: str, message: str) -> JobRecord | None:
        with self._state.lock:
            job = self._state.jobs.get(job_id)
            if job is None:
                return None
            value = deepcopy(job)
            value.logs.append(str(message))
            value.updated_at = _utcnow()
            self._save(value, "job.updated")
            return deepcopy(value)

    def _release_expired(self, now: datetime) -> None:
        for job_id, job in list(self._state.jobs.items()):
            if job.status not in _ACTIVE or job.status == JobStatus.CANCEL_REQUESTED or job.lease is None:
                continue
            if datetime.fromisoformat(job.lease.expires_at) <= now:
                value = deepcopy(job)
                value.status = JobStatus.QUEUED
                value.lease = None
                value.updated_at = _utcnow()
                self._save(value, "job.updated")

    def _save(self, job: JobRecord, event_type: str) -> None:
        self._state.jobs[job.id] = deepcopy(job)
        self._event(job.id, event_type, job.model_dump(mode="json"))

    def _event(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = JobEventRecord(
            id=self._state.next_event_id,
            job_id=job_id,
            event_type=event_type,
            payload=deepcopy(payload),
            created_at=_utcnow(),
        )
        self._state.next_event_id += 1
        self._state.events.append(event)


def reset_in_memory_job_stores() -> None:
    with _STATES_LOCK:
        _STATES.clear()
