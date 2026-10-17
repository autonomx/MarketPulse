"""Lifecycle and per-pool diagnostics for standalone job workers."""
from __future__ import annotations

import os
import time
import uuid
from typing import Any

from app.jobs.handlers import JobHandlerRegistry
from app.composition.worker_runtime.durable_feature_worker import DurableFeatureJobWorker
from app.composition.worker_runtime.pools import WorkerPoolConfig


class JobWorkerPoolRuntime:
    def __init__(
        self,
        store: Any,
        registry: JobHandlerRegistry,
        pools: tuple[WorkerPoolConfig, ...],
        *,
        services: Any = None,
        poll_seconds: float = 0.25,
        shutdown_grace_seconds: float = 30,
    ) -> None:
        if not pools:
            raise ValueError("at least one job worker pool is required")
        self.workers = {
            pool.name: DurableFeatureJobWorker(
                store,
                registry,
                pool_name=pool.name,
                resource_classes=pool.resource_classes,
                services=services,
                poll_seconds=poll_seconds,
                max_concurrency=pool.concurrency,
                shutdown_grace_seconds=shutdown_grace_seconds,
                worker_id=f"job-worker:{os.getpid()}:{uuid.uuid4().hex}:{pool.name}",
            )
            for pool in pools
        }
        self.shutdown_grace_seconds = float(shutdown_grace_seconds)

    @property
    def ready(self) -> bool:
        return bool(self.workers) and all(worker.ready for worker in self.workers.values())

    def start(self) -> None:
        for worker in self.workers.values():
            worker.start()

    def stop(self) -> None:
        deadline = time.monotonic() + self.shutdown_grace_seconds
        for worker in self.workers.values():
            worker.stop(grace_seconds=max(0.0, deadline - time.monotonic()))

    def diagnostics(self) -> dict[str, Any]:
        pools = {name: worker.diagnostics() for name, worker in self.workers.items()}
        return {
            "ready": self.ready,
            "pools": pools,
        }


__all__ = ["JobWorkerPoolRuntime"]

