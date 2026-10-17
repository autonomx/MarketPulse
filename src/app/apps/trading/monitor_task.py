"""Trading monitors are tasks on the shared scheduler (WP-8.3).

The scheduler owns the loop. A monitor inherits ``ScheduledTradingMonitor``
and exposes ``run_once`` and its cadence; it has no start, stop or loop of
its own. Each monitor module returns a ``TradingMonitorTask`` from its
``create_*_task`` factory, and ``scheduled_task_spec`` turns that into the
``ScheduledTaskSpec`` the trading feature registers.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, ClassVar

from app.runtime.scheduler import ScheduledTaskSpec, TaskContext

from .trade_logging import trade_log

# A trading cycle fetches bars and may call a model for dozens of candidates;
# at the open that legitimately takes longer than a minute.
TRADING_MONITOR_TIMEOUT_SECONDS = 300.0


class ScheduledTradingMonitor:
    """Cadence, error reporting and diagnostics shared by every trading monitor."""

    interval_seconds: float = 60.0
    # Longest a single cycle may run before the scheduler cancels it; None
    # means TRADING_MONITOR_TIMEOUT_SECONDS (or the interval, when longer).
    timeout_seconds: float | None = None
    last_error: str | None = None
    # True once the scheduler runs this monitor in this process.
    scheduled: bool = False
    # The trade-log event a failed cycle records; None records only last_error.
    error_event: ClassVar[str | None] = None
    _wake_requested: bool = False
    _last_cycle_at: float | None = None

    def tick_seconds(self) -> float:
        """How often the scheduler checks whether a cycle is due."""
        return self.interval_seconds

    def current_interval_seconds(self) -> float:
        """The time between cycles now; monitors with an adaptive cadence override it."""
        return self.interval_seconds

    def wake(self) -> None:
        """Run the next cycle at the next tick instead of waiting for the interval."""
        self._wake_requested = True

    def run_timeout_seconds(self) -> float:
        if self.timeout_seconds is not None:
            return float(self.timeout_seconds)
        return max(TRADING_MONITOR_TIMEOUT_SECONDS, self.tick_seconds())

    def cycle_due(self, now: float) -> bool:
        if self._wake_requested or self._last_cycle_at is None:
            return True
        interval = self.current_interval_seconds()
        if self.tick_seconds() >= interval:
            return True
        return now - self._last_cycle_at >= interval

    def begin_cycle(self, now: float) -> None:
        self._wake_requested = False
        self._last_cycle_at = now

    def error_log_fields(self) -> dict[str, Any]:
        return {"execution_authority": False}

    def record_cycle_error(self, exc: Exception) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"
        if self.error_event is not None:
            trade_log(
                "auto_trading",
                self.error_event,
                error_type=type(exc).__name__,
                detail=str(exc),
                **self.error_log_fields(),
            )


class SingleFlightTasks:
    """Side work a monitor starts but its cycle never awaits: at most one running task per key."""

    def __init__(self, on_error: Callable[[BaseException], None]) -> None:
        self.on_error = on_error
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def start(self, key: str, work: Callable[[], Coroutine[Any, Any, Any]]) -> bool:
        running = self._tasks.get(key)
        if running is not None and not running.done():
            return False
        task = asyncio.create_task(work())
        self._tasks[key] = task
        task.add_done_callback(self._finished)
        return True

    def _finished(self, task: asyncio.Task[Any]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            self.on_error(error)

    async def close(self) -> None:
        running = [task for task in self._tasks.values() if not task.done()]
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        self._tasks.clear()


@dataclass(frozen=True, slots=True)
class TradingMonitorTask:
    """A monitor and how the scheduler runs it."""

    name: str
    monitor: ScheduledTradingMonitor
    enabled: Callable[[], bool]
    startup: tuple[Callable[[], Any], ...] = ()
    shutdown: tuple[Callable[[], Any], ...] = ()


def scheduled_task_spec(task: TradingMonitorTask) -> ScheduledTaskSpec:
    monitor = task.monitor
    run_once = getattr(monitor, "run_once", None)
    if not callable(run_once):
        raise TypeError(f"Scheduled trading monitor {task.name} must expose run_once()")
    interval_seconds = float(monitor.tick_seconds())

    def due() -> bool:
        now = time.monotonic()
        if not monitor.cycle_due(now):
            return False
        monitor.begin_cycle(now)
        return True

    run: Callable[[TaskContext], Any]
    if inspect.iscoroutinefunction(run_once):
        async def run_async(_task_context: TaskContext) -> None:
            if not due():
                return
            try:
                await run_once()
            except Exception as exc:
                monitor.record_cycle_error(exc)
                raise

        run = run_async
        executor = "async"
    else:
        def run_sync(_task_context: TaskContext) -> None:
            if not due():
                return
            try:
                result = run_once()
            except Exception as exc:
                monitor.record_cycle_error(exc)
                raise
            if inspect.isawaitable(result):
                raise TypeError(f"Synchronous trading monitor {task.name} returned an awaitable")

        run = run_sync
        executor = "thread"

    startup = task.startup
    prepare = getattr(monitor, "prepare_for_scheduled_execution", None)
    if not startup and callable(prepare):
        startup = (prepare,)

    # The scheduler runs these only in the process that owns the task.
    def started() -> None:
        monitor.scheduled = True

    def stopped() -> None:
        monitor.scheduled = False

    return ScheduledTaskSpec(
        task_id=task.name,
        run=run,
        interval_seconds=max(0.25, interval_seconds),
        jitter_seconds=min(1.0, max(0.0, interval_seconds * 0.05)),
        timeout_seconds=monitor.run_timeout_seconds(),
        # One slow cycle (a provider stall at the open) must not stop the
        # monitor for the rest of the process lifetime; the next cycle retries.
        disable_on_timeout=False,
        executor=executor,
        enabled=task.enabled,
        on_startup=(*startup, started),
        on_shutdown=(stopped, *task.shutdown),
    )


__all__ = [
    "TRADING_MONITOR_TIMEOUT_SECONDS",
    "ScheduledTradingMonitor",
    "SingleFlightTasks",
    "TradingMonitorTask",
    "scheduled_task_spec",
]
