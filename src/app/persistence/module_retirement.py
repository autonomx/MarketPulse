"""Driving a retiring module's durable work to a final state (ADR-0016, PA-4.3).

``scripts/retire_module.py`` drains a module, then calls ``cancel_remaining``
and checks ``in_flight_work`` before it marks the module retired. Everything
here goes through the kernel repositories and records reason
``module_retired``; nothing deletes rows. The work spans every workspace, so it
runs as the ``operator.cli`` system operation.
"""
from __future__ import annotations

import fnmatch
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .capability_approval_repository import PostgresCapabilityApprovalRepository
from .identity_service import list_active_workspace_contexts
from .module_states import ModuleState, State, read_module_state, set_module_state
from .tenant import TenantContext
from .tenant_scope import system_scope
from .unit_of_work import unit_of_work

REASON = "module_retired"


@dataclass(frozen=True)
class OutboxSubscription:
    """One of the module's outbox consumers: which events it receives."""

    consumer: str
    aggregate_types: tuple[str, ...]
    event_type_pattern: str = "*"


@dataclass(frozen=True)
class RetirementSubject:
    """What a retiring module leaves in flight: its job types, outbox consumers and tools' capability ids."""

    module_id: str
    job_types: tuple[str, ...] = ()
    subscriptions: tuple[OutboxSubscription, ...] = ()
    capability_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class InFlightWork:
    active_jobs: int = 0
    waiting_jobs: int = 0
    undelivered_events: int = 0
    open_approvals: int = 0

    @property
    def drained(self) -> bool:
        """Nothing is running or waiting to be delivered: what draining waits for."""
        return self.active_jobs == 0 and self.undelivered_events == 0

    @property
    def empty(self) -> bool:
        return self.drained and self.waiting_jobs == 0 and self.open_approvals == 0


@dataclass
class CancelReport:
    canceled_jobs: int = 0
    cancel_requested_jobs: int = 0
    failed_jobs: int = 0
    dead_lettered_deliveries: int = 0
    expired_approvals: int = 0
    skipped_deliveries: list[str] = field(default_factory=list)


def _workspaces(database: Any) -> list[TenantContext]:
    """Every workspace, active or not: a suspended one's jobs are in flight too."""
    return list_active_workspace_contexts(database, include_inactive=True)


def _undelivered(connection: Any, subscription: OutboxSubscription) -> Iterator[dict[str, Any]]:
    from .outbox_repository import PostgresOutboxConsumerRepository

    inbox, after = PostgresOutboxConsumerRepository(connection), 0
    while True:
        batch = inbox.undelivered(consumer_id=subscription.consumer, aggregate_types=list(subscription.aggregate_types),
                                  after_id=after)
        for event in batch:
            if fnmatch.fnmatchcase(event["event_type"], subscription.event_type_pattern):
                yield event
        if len(batch) < 200:
            return
        after = batch[-1]["id"]


def set_state(database: Any, module_id: str, state: State, *, drain_deadline: datetime | None = None) -> ModuleState:
    with system_scope("operator.cli"), unit_of_work(database) as work:
        result = set_module_state(work.connection, module_id, state, drain_deadline=drain_deadline,
                                  reason=REASON if state != "active" else "")
        work.commit()
    return result


def reactivate(database: Any, module_id: str) -> ModuleState:
    """Undo a retirement that stopped: a draining module takes new work again. A retired module stays retired."""
    with system_scope("operator.cli"), unit_of_work(database) as work:
        if read_module_state(work.connection, module_id).state == "retired":
            raise ValueError(f"{module_id} is already retired")
        result = set_module_state(work.connection, module_id, "active")
        work.commit()
    return result


def in_flight_work(database: Any, subject: RetirementSubject) -> InFlightWork:
    """What of the module's work is not final yet, across every workspace."""
    with system_scope("operator.cli"), unit_of_work(database) as work:
        active, waiting = work.jobs.unfinished_counts(subject.job_types)
        undelivered = sum(1 for subscription in subject.subscriptions for _ in _undelivered(work.connection, subscription))
        approvals = PostgresCapabilityApprovalRepository(work.connection).open_count(list(subject.capability_ids))
        work.rollback()
    return InFlightWork(active, waiting, undelivered, approvals)


def cancel_remaining(database: Any, subject: RetirementSubject) -> CancelReport:
    """Cancel the module's jobs, dead-letter its consumers' deliveries and expire its tools' open approvals.

    Running jobs are only asked to stop here; ``fail_unfinished_jobs`` ends
    the ones still unfinished after a grace period.
    """
    report = CancelReport()
    with system_scope("operator.cli"):
        for context in _workspaces(database):
            while True:
                with unit_of_work(database) as work:
                    changed = work.jobs.cancel_jobs_of_types(context, subject.job_types, reason=REASON)
                    work.commit()
                report.canceled_jobs += sum(1 for job in changed if job["status"] == "canceled")
                report.cancel_requested_jobs += sum(1 for job in changed if job["status"] == "cancel_requested")
                if not changed:
                    break
            with unit_of_work(database) as work:
                report.expired_approvals += PostgresCapabilityApprovalRepository(work.connection).expire_for_capabilities(
                    context, list(subject.capability_ids), reason=REASON,
                )
                work.commit()
        for subscription in subject.subscriptions:
            with unit_of_work(database) as work:
                for event in list(_undelivered(work.connection, subscription)):
                    if work.outbox_consumers.dead_letter(consumer_id=subscription.consumer, event_key=event["event_key"],
                                                         reason=REASON):
                        report.dead_lettered_deliveries += 1
                    else:
                        report.skipped_deliveries.append(f"{subscription.consumer}:{event['event_key']}")
                work.commit()
    return report


def fail_unfinished_jobs(database: Any, subject: RetirementSubject) -> int:
    """Fail the module's jobs that did not stop when asked, as recovery fails a retired type's jobs."""
    failed = 0
    with system_scope("operator.cli"):
        for context in _workspaces(database):
            while True:
                with unit_of_work(database) as work:
                    batch = work.jobs.fail_retired_jobs(context, subject.job_types)
                    work.commit()
                failed += len(batch)
                if not batch:
                    break
    return failed


__all__ = [
    "CancelReport",
    "InFlightWork",
    "OutboxSubscription",
    "REASON",
    "RetirementSubject",
    "cancel_remaining",
    "fail_unfinished_jobs",
    "in_flight_work",
    "reactivate",
    "set_state",
]
