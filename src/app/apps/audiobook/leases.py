"""Short audiobook job leases kept alive by a background heartbeat.

A claimed job holds a lease of ``JOB_LEASE_SECONDS``. ``lease_heartbeat``
renews it every ``HEARTBEAT_SECONDS`` while the job runs, so a crashed worker's
job is reclaimable within about two minutes instead of an hour. The fenced
renewals inside the job's own write transactions remain the authority for
whether a result may be published; the heartbeat only keeps a live worker's
lease from expiring between those checkpoints (one long synthesis, LLM call or
encode).
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import copy_context
from typing import Any

from app.jobs.errors import JobClaimConflict
from app.persistence.tenant import TenantContext
from app.persistence.unit_of_work import unit_of_work

JOB_LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30.0

_LOG = logging.getLogger(__name__)


@contextmanager
def lease_heartbeat(
    database: Any, context: TenantContext, *, job_id: str, worker_id: str,
    lease_token: str, interval: float = HEARTBEAT_SECONDS,
) -> Iterator[None]:
    """Renew a claimed job's lease in the background until the block exits.

    Renewal stops for good once the lease is lost (expired, released or
    reclaimed); a transient database error is retried on the next beat.
    """
    stop = threading.Event()

    def renew() -> None:
        while not stop.wait(interval):
            try:
                with unit_of_work(database) as work:
                    work.jobs.renew_lease(
                        context, job_id=job_id, worker_id=worker_id,
                        lease_token=lease_token, lease_seconds=JOB_LEASE_SECONDS,
                    )
                    work.commit()
            except JobClaimConflict:
                return
            except Exception:
                _LOG.warning("audiobook lease renewal failed job_id=%s", job_id, exc_info=True)

    # Threads do not inherit context variables: keep the job's tenant.
    thread = threading.Thread(
        target=copy_context().run, args=(renew,),
        name=f"audiobook-lease-{job_id[-8:]}", daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5.0)
