"""One explicitly assigned background worker process, with supervised hooks."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
from functools import wraps
import hashlib
import inspect
import logging
import threading
import time
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, ContextManager

from .config import RuntimeConfig, GatewayRole, get_runtime_config
from .capabilities import RuntimeCapabilities, RuntimeCapability
from .logging import runtime_transition
from .ownership_fencing import claim_epoch, epoch_is_current

logger = logging.getLogger(__name__)


class BackgroundOwnershipUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BackgroundWorker:
    name: str
    monitor: object
    startup: tuple[Callable, ...]
    shutdown: tuple[Callable, ...]
    requires: frozenset[RuntimeCapability] = frozenset({RuntimeCapability.OWN_BACKGROUND_RUNTIME})


class GatewayBackgroundRuntime:
    def __init__(
        self,
        database,
        workspace_id: str,
        *,
        role: str | None = None,
        config: RuntimeConfig | None = None,
        poll_seconds: float = 2,
        authority_check: Callable[[Any], None] | None = None,
        execution_scope: Callable[[Any], ContextManager[Any]] | None = None,
    ):
        if role is not None and config is not None and role != config.gateway_role:
            raise ValueError("Background role contradicts runtime configuration")
        self.config = config or (RuntimeConfig(gateway_role=GatewayRole(role)) if role is not None else get_runtime_config())
        if poll_seconds <= 0:
            raise ValueError(
                "background role must be valid, with a positive supervision interval"
            )
        self.database = database
        self.role = self.config.gateway_role.value
        self.capabilities = RuntimeCapabilities.from_config(self.config)
        self.poll_seconds = poll_seconds
        self._authority_check = authority_check or (lambda _connection: None)
        self._execution_scope = execution_scope or (lambda _owner: nullcontext())
        digest = hashlib.sha256(
            f"omnix:gateway-background:{workspace_id}".encode()
        ).digest()
        self.lock_key = int.from_bytes(digest[:8], "big", signed=True)
        self.healthy = False
        self.epoch: int | None = None
        self.connection: Any = None
        self._connection_context: Any = None
        self._connection_lock = threading.Lock()
        self._workers: list[Any] = []
        self._started: list[Any] = []

    def acquire(self):
        if not self.config.owns_background_runtime:
            return
        self.capabilities.require(RuntimeCapability.OWN_BACKGROUND_RUNTIME)
        # Held for the process lifetime, so it must not occupy a pool slot (WP-5.10).
        self._connection_context = self.database.dedicated_connection()
        connection = self._connection_context.__enter__()
        try:
            self._authority_check(connection)
            acquired = connection.execute(
                "SELECT pg_try_advisory_lock(%s)", (self.lock_key,)
            ).fetchone()[0]
            connection.commit()
            if not acquired:
                raise BackgroundOwnershipUnavailable(
                    "Another process owns background workers; use OMNIX_GATEWAY_BACKGROUND_ROLE=api for API replicas"
                )
            self.epoch = claim_epoch(connection, self.lock_key)
            connection.commit()
            self.connection = connection
            self.healthy = True
            runtime_transition(logger, component='background_owner', role=self.role, transition='acquired')
        except BaseException:
            self._connection_context.__exit__(*__import__("sys").exc_info())
            self._connection_context = None
            raise

    def require_live(self):
        if self.config.runs_job_workers:
            return
        if self.role != "worker" or not self.healthy or self.connection is None:
            raise BackgroundOwnershipUnavailable(
                "This gateway does not own background execution"
            )
        try:
            with self._connection_lock:
                self.connection.execute("SELECT 1").fetchone()
                self.connection.commit()
        except Exception as exc:
            self.healthy = False
            raise BackgroundOwnershipUnavailable(
                "Background ownership connection was lost"
            ) from exc

    def fence(self, connection) -> None:
        """Fail the caller's transaction unless this process still owns the lock (WP-8.3).

        Runs on the caller's own connection, so background transactions no
        longer queue behind the ownership connection.
        """
        if self.config.runs_job_workers:
            return
        if not self.healthy or self.epoch is None:
            raise BackgroundOwnershipUnavailable(
                "This gateway does not own background execution"
            )
        if not epoch_is_current(connection, self.lock_key, self.epoch):
            self.healthy = False
            raise BackgroundOwnershipUnavailable(
                "Background ownership moved to another process"
            )

    def ready(self):
        if not self.config.owns_background_runtime:
            return True
        self.require_live()
        return True

    def diagnostics(self):
        return {"role": self.role, "owns_lock": self.connection is not None and self.healthy,
                "lock_healthy": self.healthy,
                "registered_workers": [worker[0] for worker in self._workers],
                "started_workers": [worker[0] for worker in self._started]}

    def register(self, name, monitor, startup, shutdown):
        self._register(
            name,
            monitor,
            startup,
            shutdown,
            frozenset({RuntimeCapability.OWN_BACKGROUND_RUNTIME}),
        )

    def _register(self, name, monitor, startup, shutdown, requires):
        if any(worker[0] == name for worker in self._workers):
            raise ValueError(f"Background worker already registered: {name}")
        # Protect manual control endpoints as well as startup hooks.
        original_start = getattr(monitor, "start", None)
        if original_start is not None:

            @wraps(original_start)
            def start(*args, **kwargs):
                self.require_live()
                with self._execution_scope(self):
                    result = original_start(*args, **kwargs)
                    service = getattr(monitor, "service", None)
                    buffer = getattr(
                        getattr(service, "binance", None), "liquidation_buffer", None
                    )
                    if buffer is not None:
                        buffer.start_guard = self.require_live
                    return result

            monitor.start = start
        self._workers.append((name, startup, shutdown, monitor, requires))

    async def _call(self, callback):
        result = callback()
        if inspect.isawaitable(result):
            await result

    async def startup(self):
        if not self.config.owns_background_runtime and not self.config.runs_job_workers:
            return
        required_role = (
            RuntimeCapability.RUN_JOB_WORKERS
            if self.config.runs_job_workers
            else RuntimeCapability.OWN_BACKGROUND_RUNTIME
        )
        self.capabilities.require(required_role)
        if self._started:
            raise RuntimeError("Background workers already started")
        if self.config.owns_background_runtime:
            self.require_live()
        for worker in self._workers:
            if required_role not in worker[4]:
                continue
            started = time.monotonic()
            self._started.append((worker[0], worker[1], worker[2]))
            with self._execution_scope(self):
                for callback in worker[1]:
                    await self._call(callback)
            runtime_transition(logger, component=worker[0], role=self.role, transition='started', started_at=started)

    async def shutdown(self):
        failed = []
        for name, _, callbacks in reversed(self._started):
            started = time.monotonic()
            for callback in reversed(callbacks):
                try:
                    await asyncio.wait_for(self._call(callback), timeout=10)
                except Exception as exc:
                    failed.append(name)
                    runtime_transition(logger, component=name, role=self.role, transition='stop_failed',
                                       started_at=started, error=exc, level='warning')
            if name not in failed:
                runtime_transition(logger, component=name, role=self.role, transition='stopped', started_at=started)
        self._started.clear()
        if failed:
            raise RuntimeError(f"Background workers did not stop: {failed}")

    async def _supervise(self):
        while True:
            await asyncio.sleep(self.poll_seconds)
            try:
                await asyncio.to_thread(self.require_live)
            except Exception as exc:
                self.healthy = False
                runtime_transition(logger, component='background_owner', role=self.role,
                                   transition='authority_lost', error=exc, level='warning')
                await self.shutdown()
                return

    def release(self):
        self.healthy = False
        context, self._connection_context = self._connection_context, None
        connection, self.connection = self.connection, None
        if context is not None:
            try:
                with self._connection_lock:
                    if not connection.closed:
                        connection.execute(
                            "SELECT pg_advisory_unlock(%s)", (self.lock_key,)
                        )
                        connection.commit()
            except Exception as exc:
                runtime_transition(logger, component='background_owner', role=self.role,
                                   transition='release_connection_lost', error=exc, level='warning')
                connection.close()
            finally:
                try:
                    context.__exit__(None, None, None)
                except Exception as exc:
                    runtime_transition(logger, component='background_owner', role=self.role,
                                       transition='pool_return_failed', error=exc, level='warning')
            runtime_transition(logger, component='background_owner', role=self.role, transition='released')

    @asynccontextmanager
    async def lifespan(self):
        await asyncio.to_thread(self.acquire)
        task = (
            asyncio.create_task(self._supervise())
            if self.config.owns_background_runtime
            else None
        )
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            try:
                await self.shutdown()
            finally:
                await asyncio.to_thread(self.release)


    def register_worker(self, worker: BackgroundWorker) -> None:
        supported = {
            RuntimeCapability.OWN_BACKGROUND_RUNTIME,
            RuntimeCapability.RUN_JOB_WORKERS,
        }
        if len(worker.requires) != 1 or not worker.requires <= supported:
            raise ValueError("Background workers must declare one supported runtime role")
        if (
            self.role == "worker"
            and RuntimeCapability.OWN_BACKGROUND_RUNTIME in worker.requires
        ) or (
            self.role == "job-worker"
            and RuntimeCapability.RUN_JOB_WORKERS in worker.requires
        ):
            self.capabilities.require(*worker.requires)
        self._register(
            worker.name,
            worker.monitor,
            worker.startup,
            worker.shutdown,
            worker.requires,
        )


class BackgroundRegistry:
    """Feature-neutral registry contract used by domain packages."""

    def register_worker(self, worker: BackgroundWorker) -> None:
        raise NotImplementedError


def register_background_worker(registry: BackgroundRegistry, worker: BackgroundWorker) -> None:
    """Register a worker without depending on FastAPI or gateway state."""
    if registry is None:
        raise BackgroundOwnershipUnavailable("Background registry is not composed")
    registry.register_worker(worker)
