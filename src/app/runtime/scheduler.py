"""Database-owned, per-task scheduler for feature and platform maintenance."""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import inspect
import logging
import os
import pickle
import random
import sys
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from .capabilities import RuntimeCapabilities, RuntimeCapability
from .logging import runtime_transition
from .ownership_fencing import claim_epoch, epoch_is_current
from .statement_class import statement_class
from .tenant_context import pop_tenant, push_tenant


class SchedulerOwnershipUnavailable(RuntimeError):
    """Raised when this process cannot safely own a scheduled task."""


class TaskExecutionTimeout(TimeoutError):
    """A task exceeded its deadline and will not be scheduled again by this owner."""


class TaskExecutor(str, Enum):
    ASYNC = "async"
    THREAD = "thread"
    PROCESS = "process"


@dataclass(frozen=True, slots=True)
class TaskContext:
    """Small, serializable task identity and timing envelope."""

    task_id: str
    workspace_id: str
    scheduled_at: datetime


@dataclass(frozen=True, slots=True)
class ScheduledTaskSpec:
    """One independently owned scheduled operation."""

    task_id: str
    run: Callable[[TaskContext], Any]
    interval_seconds: float | None = None
    cron: str | None = None
    jitter_seconds: float = 0.0
    timeout_seconds: float = 60.0
    # A timed-out run has already stopped (async work is cancelled; thread and
    # process work is waited out). True fails the task closed until restart;
    # False records the timeout as one failed run and keeps the schedule.
    disable_on_timeout: bool = True
    executor: TaskExecutor | str = TaskExecutor.ASYNC
    max_overlap: int = 1
    requires: frozenset[RuntimeCapability] = frozenset(
        {RuntimeCapability.RUN_SCHEDULERS}
    )
    enabled: Callable[[], bool] = lambda: True
    on_startup: tuple[Callable[[], Any], ...] = ()
    on_shutdown: tuple[Callable[[], Any], ...] = ()
    # Run once per active workspace, as that workspace's tenant (WP-4.2).
    per_workspace: bool = False

    def __post_init__(self) -> None:
        if self.per_workspace and self.executor in {TaskExecutor.PROCESS, TaskExecutor.PROCESS.value}:
            raise ValueError("per-workspace scheduled tasks run in the event loop or a thread, not a process")
        normalized_id = self.task_id.strip()
        if not normalized_id or normalized_id != self.task_id:
            raise ValueError("scheduled task id must be a non-empty stable identifier")
        if (self.interval_seconds is None) == (self.cron is None):
            raise ValueError("scheduled task must declare exactly one of interval_seconds or cron")
        if self.interval_seconds is not None and self.interval_seconds <= 0:
            raise ValueError("scheduled task interval must be positive")
        if self.cron is not None:
            _parse_cron(self.cron)
        if self.jitter_seconds < 0:
            raise ValueError("scheduled task jitter cannot be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("scheduled task timeout must be positive")
        if self.max_overlap != 1:
            raise ValueError("scheduled tasks currently require max_overlap=1")
        object.__setattr__(self, "executor", TaskExecutor(self.executor))
        object.__setattr__(
            self,
            "requires",
            frozenset(RuntimeCapability(value) for value in self.requires),
        )
        if self.executor is TaskExecutor.ASYNC and not (
            inspect.iscoroutinefunction(self.run)
            or inspect.iscoroutinefunction(getattr(self.run, "__call__", None))
        ):
            raise ValueError("async scheduled task run callback must be awaitable")
        if self.executor is not TaskExecutor.ASYNC:
            if inspect.iscoroutinefunction(self.run) or inspect.iscoroutinefunction(
                getattr(self.run, "__call__", None)
            ):
                raise ValueError("thread and process task callbacks must be synchronous")
        if self.executor is TaskExecutor.PROCESS:
            try:
                pickle.dumps(self.run)
            except Exception as exc:
                raise ValueError("process scheduled task callback must be pickleable") from exc


@dataclass(slots=True)
class _TaskMetrics:
    owner_process_id: int | None = None
    run_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    last_duration_seconds: float | None = None
    last_lag_seconds: float | None = None
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    last_error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "owner_process_id": self.owner_process_id,
            "run_count": self.run_count,
            "failure_count": self.failure_count,
            "timeout_count": self.timeout_count,
            "last_duration_seconds": self.last_duration_seconds,
            "last_lag_seconds": self.last_lag_seconds,
            "last_started_at": (
                self.last_started_at.isoformat() if self.last_started_at else None
            ),
            "last_completed_at": (
                self.last_completed_at.isoformat() if self.last_completed_at else None
            ),
            "last_error": self.last_error,
        }


@dataclass(slots=True)
class _TaskControls:
    enabled: asyncio.Event
    trigger: asyncio.Event
    active_run: asyncio.Event


@dataclass(frozen=True, slots=True)
class _CronField:
    values: frozenset[int]
    wildcard: bool


@dataclass(frozen=True, slots=True)
class _CronExpression:
    minute: _CronField
    hour: _CronField
    day: _CronField
    month: _CronField
    weekday: _CronField


def _parse_cron_field(value: str, low: int, high: int, *, sunday: bool = False) -> _CronField:
    wildcard = value == "*"
    expanded: set[int] = set()
    for term in value.split(","):
        if not term:
            raise ValueError("cron field contains an empty list item")
        base, slash, step_text = term.partition("/")
        if slash:
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValueError("cron step must be a positive integer")
            step = int(step_text)
        else:
            step = 1
        if base == "*":
            start, stop = low, high
        elif "-" in base:
            first, separator, last = base.partition("-")
            if not separator or not first.isdigit() or not last.isdigit():
                raise ValueError("cron range must contain integer endpoints")
            start, stop = int(first), int(last)
        elif base.isdigit():
            start = int(base)
            stop = high if slash else start
        else:
            raise ValueError("cron field contains an unsupported value")
        allowed_high = 7 if sunday else high
        if start < low or stop > allowed_high or start > stop:
            raise ValueError("cron field value is outside its supported range")
        values = range(start, stop + 1, step)
        expanded.update(0 if sunday and item == 7 else item for item in values)
    if not expanded:
        raise ValueError("cron field must select at least one value")
    return _CronField(frozenset(expanded), wildcard)


def _parse_cron(expression: str) -> _CronExpression:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("cron expressions must contain five fields: minute hour day month weekday")
    return _CronExpression(
        minute=_parse_cron_field(fields[0], 0, 59),
        hour=_parse_cron_field(fields[1], 0, 23),
        day=_parse_cron_field(fields[2], 1, 31),
        month=_parse_cron_field(fields[3], 1, 12),
        weekday=_parse_cron_field(fields[4], 0, 6, sunday=True),
    )


def next_cron_time(expression: str, after: datetime) -> datetime:
    """Return the next UTC minute matching a five-field cron expression."""
    if after.tzinfo is None:
        raise ValueError("cron timestamps must be timezone-aware")
    cron = _parse_cron(expression)
    after_utc = after.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if after_utc <= after.astimezone(timezone.utc):
        after_utc += timedelta(minutes=1)
    last_day = after_utc.date() + timedelta(days=366 * 8)
    current_day = after_utc.date()
    while current_day <= last_day:
        weekday = (current_day.weekday() + 1) % 7
        day_match = current_day.day in cron.day.values
        weekday_match = weekday in cron.weekday.values
        if cron.day.wildcard:
            calendar_match = weekday_match
        elif cron.weekday.wildcard:
            calendar_match = day_match
        else:
            calendar_match = day_match or weekday_match
        if current_day.month in cron.month.values and calendar_match:
            for hour in sorted(cron.hour.values):
                if current_day == after_utc.date() and hour < after_utc.hour:
                    continue
                for minute in sorted(cron.minute.values):
                    candidate = datetime(
                        current_day.year,
                        current_day.month,
                        current_day.day,
                        hour,
                        minute,
                        tzinfo=timezone.utc,
                    )
                    if candidate >= after_utc:
                        return candidate
        current_day += timedelta(days=1)
    raise ValueError("cron expression has no occurrence within eight years")


class _SchedulerLockSession:
    def __init__(
        self,
        database: Any,
        workspace_id: str,
        authority_check: Callable[[Any], None],
    ) -> None:
        self.database = database
        self.workspace_id = workspace_id
        self.authority_check = authority_check
        self.healthy = False
        self.connection: Any = None
        self.connection_context: AbstractContextManager[Any] | None = None
        self.connection_lock = threading.Lock()
        self.lock_keys: set[int] = set()
        self.epochs: dict[int, int] = {}

    def open(self) -> None:
        # Held for the process lifetime, so it must not occupy a pool slot (WP-5.10).
        context = self.database.dedicated_connection()
        connection = context.__enter__()
        try:
            self.authority_check(connection)
            connection.commit()
            self.connection_context = context
            self.connection = connection
            self.healthy = True
        except BaseException:
            context.__exit__(*sys.exc_info())
            raise

    def acquire(self, lock_key: int) -> bool:
        self.require_live()
        try:
            with self.connection_lock:
                acquired = self.connection.execute(
                    "SELECT pg_try_advisory_lock(%s)", (lock_key,)
                ).fetchone()[0]
                self.connection.commit()
                if acquired:
                    self.lock_keys.add(lock_key)
                    self.epochs[lock_key] = claim_epoch(self.connection, lock_key)
                    self.connection.commit()
                return bool(acquired)
        except Exception:
            self.healthy = False
            raise

    def require_live(self, task_id: str = "scheduler") -> None:
        if not self.healthy or self.connection is None:
            raise SchedulerOwnershipUnavailable(
                f"Scheduler ownership is unavailable for {task_id}"
            )
        try:
            with self.connection_lock:
                if self.connection.closed:
                    raise RuntimeError("scheduler ownership connection is closed")
                self.connection.execute("SELECT 1").fetchone()
                self.connection.commit()
        except Exception as exc:
            self.healthy = False
            raise SchedulerOwnershipUnavailable(
                f"Scheduler ownership connection was lost for {task_id}"
            ) from exc

    def release(self, lock_key: int) -> None:
        if lock_key not in self.lock_keys:
            return
        try:
            with self.connection_lock:
                if self.connection is None or self.connection.closed:
                    self.healthy = False
                    self.lock_keys.discard(lock_key)
                    self.epochs.pop(lock_key, None)
                    return
                self.connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (lock_key,)
                )
                self.connection.commit()
                self.lock_keys.discard(lock_key)
                self.epochs.pop(lock_key, None)
        except Exception:
            self.healthy = False

    def close(self) -> None:
        self.healthy = False
        context, self.connection_context = self.connection_context, None
        connection, self.connection = self.connection, None
        if context is None:
            return
        try:
            with self.connection_lock:
                if connection is not None and not connection.closed:
                    for lock_key in tuple(self.lock_keys):
                        connection.execute(
                            "SELECT pg_advisory_unlock(%s)", (lock_key,)
                        )
                        connection.commit()
                    self.lock_keys.clear()
                    self.epochs.clear()
        except Exception:
            if connection is not None:
                connection.close()
        finally:
            context.__exit__(None, None, None)


class _TaskOwner:
    def __init__(
        self,
        lock_session: _SchedulerLockSession,
        task_id: str,
        lock_key: int,
    ) -> None:
        self.lock_session = lock_session
        self.task_id = task_id
        self.lock_key = lock_key
        self.healthy = False

    def acquire(self) -> bool:
        self.healthy = self.lock_session.acquire(self.lock_key)
        return self.healthy

    def require_live(self) -> None:
        if not self.healthy:
            raise SchedulerOwnershipUnavailable(
                f"Scheduler ownership is unavailable for {self.task_id}"
            )
        self.lock_session.require_live(self.task_id)

    def fence(self, connection: Any) -> None:
        """Fail the caller's transaction unless this task still owns its lock (WP-8.3)."""
        epoch = self.lock_session.epochs.get(self.lock_key)
        if not self.healthy or epoch is None:
            raise SchedulerOwnershipUnavailable(
                f"Scheduler ownership is unavailable for {self.task_id}"
            )
        if not epoch_is_current(connection, self.lock_key, epoch):
            self.healthy = False
            raise SchedulerOwnershipUnavailable(
                f"Scheduler ownership moved to another process for {self.task_id}"
            )

    def release(self) -> None:
        if not self.healthy:
            return
        self.healthy = False
        self.lock_session.release(self.lock_key)


class SchedulerRuntime:
    """Run only tasks whose task/workspace PostgreSQL advisory lock is held."""

    def __init__(
        self,
        database: Any,
        workspace_id: str,
        *,
        capabilities: RuntimeCapabilities,
        authority_check: Callable[[Any], None] | None = None,
        execution_scope: Callable[[Any], AbstractContextManager[Any]],
        thread_workers: int = 4,
        process_workers: int = 2,
        poll_seconds: float = 1.0,
        startup_jitter_seconds: float = 0.5,
        logger: logging.Logger | None = None,
        workspace_contexts: Callable[[], Sequence[Any]] | None = None,
    ) -> None:
        if not workspace_id:
            raise ValueError("scheduler workspace id is required")
        if thread_workers < 1 or process_workers < 1 or poll_seconds <= 0:
            raise ValueError("scheduler executor sizes and poll interval must be positive")
        if startup_jitter_seconds < 0:
            raise ValueError("scheduler startup jitter cannot be negative")
        self.database = database
        self.workspace_id = workspace_id
        self.workspace_contexts = workspace_contexts
        self.capabilities = capabilities
        self.authority_check = authority_check or (lambda _connection: None)
        self.execution_scope = execution_scope
        self.thread_workers = thread_workers
        self.process_workers = process_workers
        self.poll_seconds = poll_seconds
        self.startup_jitter_seconds = startup_jitter_seconds
        self.logger = logger or logging.getLogger(__name__)
        self._specs: dict[str, ScheduledTaskSpec] = {}
        self._owners: dict[str, _TaskOwner] = {}
        self._lock_session: _SchedulerLockSession | None = None
        self._runner_tasks: dict[str, asyncio.Task[None]] = {}
        self._controls: dict[str, _TaskControls] = {}
        self._metrics: dict[str, _TaskMetrics] = {}
        self._disabled_tasks: set[str] = set()
        self._thread_executor: ThreadPoolExecutor | None = None
        self._process_executor: ProcessPoolExecutor | None = None
        self._started = False
        self._stopping = False

    def register_task(self, spec: ScheduledTaskSpec) -> None:
        if not isinstance(spec, ScheduledTaskSpec):
            raise TypeError("scheduled task registry accepts ScheduledTaskSpec values")
        if self._started:
            raise RuntimeError("cannot register scheduled tasks after scheduler startup")
        if spec.task_id in self._specs:
            raise ValueError(f"Scheduled task already registered: {spec.task_id}")
        if RuntimeCapability.RUN_SCHEDULERS not in spec.requires:
            raise ValueError("scheduled tasks must require RUN_SCHEDULERS")
        self._specs[spec.task_id] = spec
        self._metrics[spec.task_id] = _TaskMetrics()

    def diagnostics(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "scheduler_enabled": self.capabilities.allows(RuntimeCapability.RUN_SCHEDULERS),
            "registered_tasks": sorted(self._specs),
            "owned_tasks": sorted(self._owners),
            "tasks": {task_id: metric.snapshot() for task_id, metric in self._metrics.items()},
        }

    def ready(self) -> bool:
        return (
            not self.capabilities.allows(RuntimeCapability.RUN_SCHEDULERS)
            or (
                self._started
                and (self._lock_session is None or self._lock_session.healthy)
            )
        )

    def owns_task(self, task_id: str) -> bool:
        return task_id in self._owners and self._owners[task_id].healthy

    async def pause_task(self, task_id: str) -> None:
        if not self.capabilities.allows(RuntimeCapability.RUN_SCHEDULERS):
            raise SchedulerOwnershipUnavailable(
                "This process does not have scheduler control authority"
            )
        controls = self._controls.get(task_id)
        if controls is None or not self.owns_task(task_id):
            raise SchedulerOwnershipUnavailable(
                f"This process does not own scheduled task {task_id}"
            )
        controls.enabled.clear()
        # Wake a timer that is sleeping until the next interval so it can
        # observe the disabled state before starting another run.
        controls.trigger.set()
        await controls.active_run.wait()

    def resume_task(self, task_id: str) -> None:
        if not self.capabilities.allows(RuntimeCapability.RUN_SCHEDULERS):
            raise SchedulerOwnershipUnavailable(
                "This process does not have scheduler control authority"
            )
        controls = self._controls.get(task_id)
        if controls is None or not self.owns_task(task_id):
            raise SchedulerOwnershipUnavailable(
                f"This process does not own scheduled task {task_id}"
            )
        controls.enabled.set()
        controls.trigger.set()

    async def startup(self) -> None:
        if self._started:
            raise RuntimeError("Scheduler already started")
        self._started = True
        if not self.capabilities.allows(RuntimeCapability.RUN_SCHEDULERS):
            return
        self._thread_executor = ThreadPoolExecutor(
            max_workers=self.thread_workers, thread_name_prefix="omnix-scheduler"
        )
        self._process_executor = ProcessPoolExecutor(max_workers=self.process_workers)
        self._lock_session = _SchedulerLockSession(
            self.database,
            self.workspace_id,
            self.authority_check,
        )
        try:
            await asyncio.to_thread(self._lock_session.open)
        except BaseException:
            await self.shutdown()
            raise
        task_ids = list(self._specs)
        random.SystemRandom().shuffle(task_ids)
        for task_id in task_ids:
            if self.startup_jitter_seconds:
                await asyncio.sleep(
                    random.uniform(0, self.startup_jitter_seconds)
                )
            try:
                await self._try_start_task(task_id)
            except SchedulerOwnershipUnavailable:
                if self._lock_session is not None and not self._lock_session.healthy:
                    await self.shutdown()
                raise
        runtime_transition(
            self.logger,
            component="scheduler",
            role="scheduler",
            transition="started",
        )

    async def _try_start_task(self, task_id: str) -> bool:
        if (
            self._stopping
            or task_id in self._owners
            or task_id in self._disabled_tasks
            or self._lock_session is None
            or not self._lock_session.healthy
        ):
            return False
        spec = self._specs[task_id]
        if not all(self.capabilities.allows(value) for value in spec.requires):
            runtime_transition(
                self.logger,
                component=task_id,
                role="scheduler",
                transition="required_capability_missing",
                level="warning",
            )
            return False
        try:
            if not spec.enabled():
                return False
        except Exception as exc:
            self._record_failure(task_id, exc)
            return False
        workspace_bytes = self.workspace_id.encode("utf-8")
        lock_digest = hashlib.sha256(
            b"omnix:scheduled-task:v1:"
            + len(workspace_bytes).to_bytes(4, "big")
            + workspace_bytes
            + task_id.encode("utf-8")
        ).digest()
        owner = _TaskOwner(
            self._lock_session,
            task_id,
            int.from_bytes(lock_digest[:8], "big", signed=True),
        )
        try:
            acquired = await asyncio.to_thread(owner.acquire)
        except Exception as exc:
            self._record_failure(task_id, exc)
            if not self._lock_session.healthy:
                raise SchedulerOwnershipUnavailable(
                    "Scheduler ownership session became unavailable"
                ) from exc
            runtime_transition(
                self.logger,
                component=task_id,
                role="scheduler",
                transition="ownership_failed",
                error=exc,
                level="warning",
            )
            return False
        if not acquired:
            runtime_transition(
                self.logger,
                component=task_id,
                role="scheduler",
                transition="owned_elsewhere",
            )
            return False
        self._owners[task_id] = owner
        self._metrics[task_id].owner_process_id = os.getpid()
        controls = _TaskControls(
            enabled=asyncio.Event(),
            trigger=asyncio.Event(),
            active_run=asyncio.Event(),
        )
        controls.enabled.set()
        controls.active_run.set()
        self._controls[task_id] = controls
        self._runner_tasks[task_id] = asyncio.create_task(
            self._run_task(spec, owner, controls), name=f"scheduled:{task_id}"
        )
        return True

    async def shutdown(self) -> None:
        if not self._started:
            return
        self._stopping = True
        tasks = tuple(self._runner_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._runner_tasks.clear()
        self._owners.clear()
        self._controls.clear()
        self._disabled_tasks.clear()
        if self._thread_executor is not None:
            self._thread_executor.shutdown(wait=True, cancel_futures=True)
            self._thread_executor = None
        if self._process_executor is not None:
            self._process_executor.shutdown(wait=True, cancel_futures=True)
            self._process_executor = None
        if self._lock_session is not None:
            await asyncio.to_thread(self._lock_session.close)
            self._lock_session = None
        self._started = False
        self._stopping = False

    async def _supervise(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.poll_seconds)
            lock_session = self._lock_session
            if lock_session is None or not lock_session.healthy:
                return
            for task_id, owner in tuple(self._owners.items()):
                if task_id not in self._runner_tasks:
                    continue
                try:
                    await asyncio.to_thread(owner.require_live)
                except Exception as exc:
                    self._record_failure(task_id, exc)
                    runtime_transition(
                        self.logger,
                        component=task_id,
                        role="scheduler",
                        transition="authority_lost",
                        error=exc,
                        level="warning",
                    )
                    runner = self._runner_tasks.pop(task_id)
                    runner.cancel()
                    await asyncio.gather(runner, return_exceptions=True)
                    await asyncio.to_thread(owner.release)
                    self._owners.pop(task_id, None)
                    self._controls.pop(task_id, None)
                    if not lock_session.healthy:
                        break
            if not lock_session.healthy:
                return
            task_ids = list(self._specs)
            random.SystemRandom().shuffle(task_ids)
            for task_id in task_ids:
                if task_id in self._owners or task_id in self._disabled_tasks:
                    continue
                try:
                    await self._try_start_task(task_id)
                except SchedulerOwnershipUnavailable as exc:
                    self._record_failure(task_id, exc)
                    runtime_transition(
                        self.logger,
                        component=task_id,
                        role="scheduler",
                        transition="ownership_unavailable",
                        error=exc,
                        level="warning",
                    )
                    if not lock_session.healthy:
                        return

    async def _run_callbacks(
        self,
        callbacks: Sequence[Callable[[], Any]],
        owner: _TaskOwner,
        *,
        timeout_seconds: float,
    ) -> None:
        async def invoke() -> None:
            with self.execution_scope(owner):
                for callback in callbacks:
                    result = callback()
                    if inspect.isawaitable(result):
                        await result

        if not callbacks:
            return
        task = asyncio.create_task(invoke())
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise TaskExecutionTimeout(
                f"scheduled task lifecycle callback exceeded {timeout_seconds}s"
            )
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    def _workspace_runs(self, spec: ScheduledTaskSpec, context: TaskContext) -> list[tuple[TaskContext, Any]]:
        """``(context, tenant)`` per run: one per active workspace, or the scheduler's own."""
        if not spec.per_workspace or self.workspace_contexts is None:
            return [(context, None)]
        return [
            (replace(context, workspace_id=tenant.workspace_id), tenant)
            for tenant in self.workspace_contexts()
        ]

    async def _invoke(
        self, spec: ScheduledTaskSpec, context: TaskContext, owner: _TaskOwner
    ) -> None:
        runs = self._workspace_runs(spec, context)
        if spec.executor is TaskExecutor.ASYNC:
            async def invoke_async() -> None:
                errors: list[Exception] = []
                with self.execution_scope(owner), statement_class("maintenance"):
                    for run_context, tenant in runs:
                        token = push_tenant(tenant) if tenant is not None else None
                        try:
                            result = spec.run(run_context)
                            if not inspect.isawaitable(result):
                                raise TypeError("async scheduled task returned a non-awaitable value")
                            await result
                        except Exception as exc:
                            # One workspace's failure must not skip the others.
                            errors.append(exc)
                        finally:
                            if token is not None:
                                pop_tenant(token)
                if errors:
                    raise errors[0]

            task = asyncio.create_task(invoke_async())
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=spec.timeout_seconds)
            except asyncio.TimeoutError as exc:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise TaskExecutionTimeout(
                    f"scheduled task {spec.task_id} exceeded {spec.timeout_seconds}s"
                ) from exc
            except asyncio.CancelledError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
            return

        executor = (
            self._thread_executor
            if spec.executor is TaskExecutor.THREAD
            else self._process_executor
        )
        if executor is None:
            raise RuntimeError("scheduler executor is not available")
        loop = asyncio.get_running_loop()
        if spec.executor is TaskExecutor.THREAD:
            def invoke_thread():
                errors: list[Exception] = []
                result = None
                with self.execution_scope(owner), statement_class("maintenance"):
                    for run_context, tenant in runs:
                        token = push_tenant(tenant) if tenant is not None else None
                        try:
                            result = spec.run(run_context)
                        except Exception as exc:
                            errors.append(exc)
                        finally:
                            if token is not None:
                                pop_tenant(token)
                if errors:
                    raise errors[0]
                return result

            future = loop.run_in_executor(executor, invoke_thread)
        else:
            future = loop.run_in_executor(executor, spec.run, context)
        try:
            result = await asyncio.wait_for(
                asyncio.shield(future), timeout=spec.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            # Threads cannot be safely killed. Keep task ownership until the
            # callback has actually exited, then fail closed without rescheduling.
            cancelled = await self._wait_for_executor_completion(future)
            if cancelled:
                raise asyncio.CancelledError
            raise TaskExecutionTimeout(
                f"scheduled task {spec.task_id} exceeded {spec.timeout_seconds}s"
            ) from exc
        except asyncio.CancelledError:
            # Cancellation cannot stop a running native thread or process callback.
            await self._wait_for_executor_completion(future)
            raise
        if inspect.isawaitable(result):
            raise TypeError("thread and process scheduled tasks must return synchronously")

    async def _wait_for_executor_completion(self, future: asyncio.Future[Any]) -> bool:
        """Wait out native work without cancelling its asyncio wrapper."""
        cancellation_received = False
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                if future.cancelled():
                    break
                cancellation_received = True
            except BaseException:
                break
        return cancellation_received

    async def _wait_until_due(
        self,
        controls: _TaskControls,
        due_monotonic: float,
        due_at: datetime,
    ) -> tuple[datetime, float, bool]:
        while True:
            await controls.enabled.wait()
            if controls.trigger.is_set():
                controls.trigger.clear()
                if not controls.enabled.is_set():
                    continue
                now = datetime.now(timezone.utc)
                return now, 0.0, True
            delay = max(0.0, due_monotonic - time.monotonic())
            if delay == 0:
                return due_at, max(0.0, time.monotonic() - due_monotonic), False
            sleep_task = asyncio.create_task(asyncio.sleep(delay))
            trigger_task = asyncio.create_task(controls.trigger.wait())
            done, pending = await asyncio.wait(
                (sleep_task, trigger_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if trigger_task in done and controls.trigger.is_set():
                controls.trigger.clear()
                if not controls.enabled.is_set():
                    continue
                now = datetime.now(timezone.utc)
                return now, 0.0, True
            if not controls.enabled.is_set():
                continue
            lag = max(0.0, time.monotonic() - due_monotonic)
            return due_at, lag, False

    async def _run_task(
        self,
        spec: ScheduledTaskSpec,
        owner: _TaskOwner,
        controls: _TaskControls,
    ) -> None:
        task_id = spec.task_id
        metric = self._metrics[task_id]
        started_at = time.monotonic()
        task_started = False
        failure: BaseException | None = None
        try:
            if not spec.enabled():
                return
            task_started = True
            await self._run_callbacks(
                spec.on_startup,
                owner,
                timeout_seconds=spec.timeout_seconds,
            )
            runtime_transition(
                self.logger,
                component=task_id,
                role="scheduler",
                transition="task_started",
                started_at=started_at,
            )
            next_interval = (
                time.monotonic() + random.uniform(0, spec.jitter_seconds)
                if spec.interval_seconds is not None
                else None
            )
            next_cron = (
                next_cron_time(spec.cron, datetime.now(timezone.utc))
                if spec.cron is not None
                else None
            )
            while not self._stopping:
                owner.require_live()
                await controls.enabled.wait()
                if next_interval is not None:
                    assert spec.interval_seconds is not None
                    interval_seconds = spec.interval_seconds
                    scheduled_mono = next_interval
                    delay = max(0.0, scheduled_mono - time.monotonic())
                    scheduled_at, lag, triggered = await self._wait_until_due(
                        controls,
                        scheduled_mono,
                        datetime.now(timezone.utc) + timedelta(seconds=delay),
                    )
                    next_interval = (
                        time.monotonic()
                        if triggered
                        else scheduled_mono + interval_seconds
                    )
                    now_mono = time.monotonic()
                    while next_interval <= now_mono:
                        next_interval += interval_seconds
                    if spec.jitter_seconds:
                        next_interval += random.uniform(0, spec.jitter_seconds)
                else:
                    assert spec.cron is not None and next_cron is not None
                    scheduled_at, lag, triggered = await self._wait_until_due(
                        controls,
                        time.monotonic()
                        + max(
                            0.0,
                            (next_cron - datetime.now(timezone.utc)).total_seconds(),
                        )
                        + random.uniform(0, spec.jitter_seconds),
                        next_cron,
                    )
                    next_cron = next_cron_time(spec.cron, scheduled_at)
                if self._stopping:
                    return
                run_context = TaskContext(
                    task_id=task_id,
                    workspace_id=self.workspace_id,
                    scheduled_at=scheduled_at,
                )
                run_started = time.monotonic()
                metric.last_started_at = datetime.now(timezone.utc)
                metric.last_lag_seconds = lag
                controls.active_run.clear()
                try:
                    await self._invoke(spec, run_context, owner)
                except TaskExecutionTimeout as exc:
                    if spec.disable_on_timeout:
                        raise
                    metric.timeout_count += 1
                    self._record_failure(task_id, exc)
                    runtime_transition(
                        self.logger,
                        component=task_id,
                        role="scheduler",
                        transition="run_timed_out",
                        started_at=run_started,
                        error=exc,
                        level="warning",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._record_failure(task_id, exc)
                    runtime_transition(
                        self.logger,
                        component=task_id,
                        role="scheduler",
                        transition="run_failed",
                        started_at=run_started,
                        error=exc,
                        level="warning",
                    )
                finally:
                    controls.active_run.set()
                    metric.last_duration_seconds = time.monotonic() - run_started
                    metric.last_completed_at = datetime.now(timezone.utc)
                    metric.run_count += 1
        except TaskExecutionTimeout as exc:
            failure = exc
            self._disabled_tasks.add(task_id)
            metric.timeout_count += 1
            self._record_failure(task_id, exc)
            runtime_transition(
                self.logger,
                component=task_id,
                role="scheduler",
                transition="task_timed_out",
                started_at=started_at,
                error=exc,
                level="warning",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = exc
            self._record_failure(task_id, exc)
            runtime_transition(
                self.logger,
                component=task_id,
                role="scheduler",
                transition="task_failed",
                started_at=started_at,
                error=exc,
                level="warning",
            )
        finally:
            try:
                if task_started:
                    await self._run_callbacks(
                        spec.on_shutdown,
                        owner,
                        timeout_seconds=min(spec.timeout_seconds, 30.0),
                    )
            except BaseException as exc:
                failure = failure or exc
                self._record_failure(task_id, exc)
            finally:
                await asyncio.to_thread(owner.release)
                self._owners.pop(task_id, None)
                self._controls.pop(task_id, None)
                self._runner_tasks.pop(task_id, None)
            runtime_transition(
                self.logger,
                component=task_id,
                role="scheduler",
                transition="task_stopped" if failure is None else "task_stopped_with_error",
                error=failure,
                level="warning" if failure is not None else "info",
            )

    def _record_failure(self, task_id: str, error: BaseException) -> None:
        metric = self._metrics[task_id]
        metric.failure_count += 1
        # Detailed exception messages stay in structured logs; readiness and
        # diagnostics may be visible beyond trusted operators.
        metric.last_error = type(error).__name__

    def lifespan(self):
        """Return an async context manager for task ownership and supervision."""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def manage():
            await self.startup()
            supervisor = (
                asyncio.create_task(self._supervise(), name="scheduler:supervise")
                if self.capabilities.allows(RuntimeCapability.RUN_SCHEDULERS)
                else None
            )
            try:
                yield self
            finally:
                if supervisor is not None:
                    supervisor.cancel()
                    await asyncio.gather(supervisor, return_exceptions=True)
                await self.shutdown()

        return manage()


__all__ = [
    "ScheduledTaskSpec",
    "SchedulerOwnershipUnavailable",
    "SchedulerRuntime",
    "TaskContext",
    "TaskExecutionTimeout",
    "TaskExecutor",
    "next_cron_time",
]
