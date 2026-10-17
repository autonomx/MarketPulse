"""Typed job-handler registry owned by the jobs kernel."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import builtins
import logging
import math
import random
from threading import Event
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ConfigDict

from .models import CreateJobRequest, FailJobRequest, JobRecord, ResourceClass


# The job a handler is running in this context: (job id, job type). A job it
# submits is a follow-up, which a draining module still accepts (PA-4.3).
_EXECUTING_JOB: ContextVar[tuple[str, str] | None] = ContextVar("omnix_executing_job", default=None)


def executing_job() -> tuple[str, str] | None:
    """``(job id, job type)`` of the handler running in this context, if any."""
    return _EXECUTING_JOB.get()


class AnyJobInput(BaseModel):
    model_config = ConfigDict(extra="allow")


JobInputModel = builtins.type[BaseModel]


@dataclass(frozen=True, slots=True)
class Backoff:
    base_seconds: float = 2.0
    factor: float = 2.0
    max_seconds: float = 300.0
    jitter: float = 0.2

    def delay(self, attempt: int, *, random_value: float | None = None) -> float:
        bounded_attempt = max(0, int(attempt))
        raw = min(self.max_seconds, self.base_seconds * (self.factor ** bounded_attempt))
        if self.jitter <= 0:
            return raw
        unit = random.random() if random_value is None else max(0.0, min(1.0, random_value))
        return max(0.0, raw * (1.0 + ((unit * 2.0) - 1.0) * self.jitter))


@dataclass(slots=True)
class JobExecutionContext:
    job_store: Any
    services: Any = None
    cancellation: Event | None = None

    def cancelled(self) -> bool:
        return bool(self.cancellation and self.cancellation.is_set())


JobHandler = Callable[[JobExecutionContext, JobRecord], JobRecord]
SubmissionPolicy = Callable[[CreateJobRequest], CreateJobRequest]
logger = logging.getLogger(__name__)


class JobObserver(Protocol):
    """Read-only notification port for durable job lifecycle transitions."""

    def on_created(self, job: JobRecord) -> None: ...
    def on_started(self, job: JobRecord) -> None: ...
    def on_completed(self, job: JobRecord) -> None: ...
    def on_failed(self, job: JobRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class JobHandlerSpec:
    type: str
    handler: JobHandler
    input_model: JobInputModel = AnyJobInput
    resource_class: ResourceClass = ResourceClass.CPU
    timeout_seconds: float = 300.0
    max_attempts: int = 3
    retry_backoff: Backoff = Backoff()
    submission_policy: SubmissionPolicy | None = None

    def __post_init__(self) -> None:
        if not self.type.strip() or self.type != self.type.strip():
            raise ValueError("job handler type must be a non-empty normalized string")
        if self.timeout_seconds <= 0:
            raise ValueError("job handler timeout must be positive")
        if self.max_attempts < 1:
            raise ValueError("job handler max_attempts must be positive")


class JobHandlerRegistry:
    def __init__(self, specs: tuple[JobHandlerSpec, ...] = ()) -> None:
        self._handlers: dict[str, JobHandlerSpec] = {}
        self._owners: dict[str, str] = {}
        self._observers: list[JobObserver] = []
        for spec in specs:
            self.register(spec)

    @property
    def observers(self) -> tuple[JobObserver, ...]:
        return tuple(self._observers)

    def register_observer(self, observer: JobObserver) -> None:
        if observer in self._observers:
            raise ValueError("duplicate job observer")
        self._observers.append(observer)

    def notify_observers(self, event: str, job: JobRecord) -> None:
        callback_name = f"on_{event}"
        for observer in self._observers:
            callback = getattr(observer, callback_name, None)
            if not callable(callback):
                raise ValueError(f"unsupported job lifecycle event: {event}")
            try:
                callback(job)
            except Exception:
                logger.exception(
                    "Job observer failed for lifecycle event %s", event
                )

    def register(self, spec: JobHandlerSpec, *, owner: str | None = None) -> None:
        """Register a handler; ``owner`` is the module whose lifecycle state governs its jobs (PA-4.3)."""
        if spec.type in self._handlers:
            raise ValueError(f"duplicate job handler type: {spec.type}")
        self._handlers[spec.type] = spec
        if owner:
            self._owners[spec.type] = owner

    def owner(self, job_type: str) -> str | None:
        """The module that registered ``job_type``, or ``None`` for kernel and unknown types."""
        return self._owners.get(job_type)

    def get(self, job_type: str) -> JobHandlerSpec | None:
        return self._handlers.get(job_type)

    def require(self, job_type: str) -> JobHandlerSpec:
        spec = self.get(job_type)
        if spec is None:
            raise KeyError(job_type)
        return spec

    def types(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def resource_classes(self) -> tuple[str, ...]:
        return tuple(sorted({spec.resource_class.value for spec in self._handlers.values()}))

    def retry_delay_seconds(self, job_type: str, attempt_count: int) -> int:
        spec = self.get(job_type)
        if spec is None:
            return 1
        attempt = max(0, int(attempt_count) - 1)
        return max(1, math.ceil(spec.retry_backoff.delay(attempt)))

    def validate_submission(self, request: CreateJobRequest) -> CreateJobRequest:
        spec = self.get(request.type)
        if spec is None:
            return request
        updated = (
            spec.submission_policy(request)
            if spec.submission_policy is not None
            else request
        )
        payload = spec.input_model.model_validate(updated.input_payload or {})
        return updated.model_copy(
            update={
                "input_payload": payload.model_dump(mode="python"),
                "resource_class": spec.resource_class,
            }
        )

    def execute(self, context: JobExecutionContext, job: JobRecord) -> JobRecord:
        spec = self.require(job.type)
        payload = spec.input_model.model_validate(job.input_payload or {})
        if payload.model_dump(mode="python") != (job.input_payload or {}):
            job = job.model_copy(update={"input_payload": payload.model_dump(mode="python")})
        token = _EXECUTING_JOB.set((str(getattr(job, "id", "")), str(job.type)))
        try:
            return spec.handler(context, job)
        finally:
            _EXECUTING_JOB.reset(token)


class RetryPolicyJobStore:
    """Apply a feature's retry schedule to retryable handler failures."""

    def __init__(
        self,
        job_store: Any,
        registry: JobHandlerRegistry,
        job: JobRecord,
    ) -> None:
        self._job_store = job_store
        self._registry = registry
        self._job_type = job.type
        self._attempt_count = max(1, int(getattr(job, "_attempt_count", 0) or 1))

    def fail_job(self, job_id: str, request: FailJobRequest) -> JobRecord | None:
        if request.retryable:
            delay = self._registry.retry_delay_seconds(
                self._job_type,
                self._attempt_count,
            )
            request = request.model_copy(deep=True)
            request._retry_delay_seconds = max(1, request._retry_delay_seconds, delay)
        return self._job_store.fail_job(job_id, request)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._job_store, name)


def registry_from_features(features: tuple[Any, ...]) -> JobHandlerRegistry:
    registry = JobHandlerRegistry()
    for feature in features:
        for spec in getattr(feature, "job_handlers", ()):
            registry.register(spec, owner=getattr(feature, "id", None))
        for observer_factory in getattr(feature, "job_observers", ()):
            observer = observer_factory()
            if observer is not None:
                registry.register_observer(observer)
    return registry
