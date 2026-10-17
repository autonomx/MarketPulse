"""Small in-process executor seam for local-first job handlers."""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .models import ClaimJobRequest, CompleteJobRequest, FailJobRequest, JobRecord, ResourceClass

HandlerResult = Mapping[str, Any] | None
JobHandler = Callable[[JobRecord], HandlerResult | Awaitable[HandlerResult]]


class LocalJobExecutor:
    """Claim one job at a time and dispatch it to registered local handlers."""

    def __init__(
        self,
        store: Any,
        handlers: Mapping[str, JobHandler],
        *,
        worker_id: str = "local:executor",
        cpu_limit: int = 2,
    ) -> None:
        self.store = store
        self.handlers = dict(handlers)
        self.worker_id = worker_id
        self.cpu_limit = cpu_limit

    async def run_once(self, resource_classes: list[ResourceClass] | None = None) -> JobRecord | None:
        claim = self.store.claim_next(
            ClaimJobRequest(
                worker_id=self.worker_id,
                resource_classes=resource_classes or [],
                cpu_limit=self.cpu_limit,
            )
        )
        if not claim.ok or claim.job is None:
            return None

        # Retain the credentials from this claim, never from a later row read.
        credentials: dict[str, Any] = {
            "worker_id": self.worker_id,
            "lease_token": claim.job.lease.token if claim.job.lease is not None else None,
        }
        job = self.store.mark_running(claim.job.id, **credentials) or claim.job
        handler = self.handlers.get(job.type)
        if handler is None:
            return self.store.fail_job(
                job.id,
                FailJobRequest(
                    **credentials,
                    code="job_handler_missing",
                    message=f"No local job handler is registered for {job.type}.",
                    retryable=False,
                ),
            )

        try:
            result = handler(job)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            return self.store.fail_job(
                job.id,
                FailJobRequest(**credentials, code="job_handler_failed", message=str(exc), retryable=False),
            )

        output_refs = list((result or {}).get("output_refs", []))
        logs = list((result or {}).get("logs", []))
        return self.store.complete_job(job.id, CompleteJobRequest(**credentials, output_refs=output_refs, logs=logs))
