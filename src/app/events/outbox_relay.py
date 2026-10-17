"""Outbox relay: deliver committed domain events to consumers (WP-5.3).

Writers append to ``omnix_outbox_events`` in the transaction that changes
their aggregate. The relay, a scheduled task, then:

1. claims a batch of pending rows (``claim_batch``; per-aggregate order is
   kept by the ordering key);
2. runs every consumer registered for the row's aggregate type, each in its
   own transaction, recording the delivery in the consumer inbox so that a
   retried row never runs a consumer twice;
3. marks the row published, or schedules a retry with backoff, or moves it
   to the dead letters once a consumer has failed ``max_attempts`` times.

Consumers are registered by the kernel (``kernel_outbox_consumers``) and by
features (``FeatureModule.outbox_consumers``).
"""
from __future__ import annotations

import fnmatch
import logging
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.persistence.outbox_repository import PostgresOutboxConsumerRepository, PostgresOutboxRepository
from app.persistence.tenant_scope import system_scope

logger = logging.getLogger(__name__)

RELAY_ID = "outbox-relay"
SYSTEM_OPERATION = "outbox.relay"
DEFAULT_BATCH_SIZE = 100
DEFAULT_MAX_ATTEMPTS = 5
MAX_RETRY_DELAY_SECONDS = 300

Handler = Callable[[Any, dict[str, Any]], "dict[str, Any] | None"]


@dataclass(frozen=True, slots=True)
class OutboxConsumer:
    """A durable consumer of outbox events.

    ``handler(connection, event)`` runs inside the consumer's transaction;
    work it does on ``connection`` commits together with its inbox record.
    """

    consumer_name: str
    aggregate_types: frozenset[str]
    handler: Handler
    event_type_pattern: str = "*"

    def matches(self, event: dict[str, Any]) -> bool:
        return event["aggregate_type"] in self.aggregate_types and fnmatch.fnmatchcase(
            event["event_type"], self.event_type_pattern
        )


class OutboxConsumerRegistry:
    def __init__(self, consumers: Iterable[OutboxConsumer] = ()) -> None:
        self._consumers: dict[str, OutboxConsumer] = {}
        for consumer in consumers:
            self.register(consumer)

    def register(self, consumer: OutboxConsumer) -> None:
        if not isinstance(consumer, OutboxConsumer):
            raise TypeError("outbox consumers must be OutboxConsumer instances")
        existing = self._consumers.get(consumer.consumer_name)
        if existing is not None and existing != consumer:
            raise ValueError(f"duplicate outbox consumer: {consumer.consumer_name}")
        self._consumers[consumer.consumer_name] = consumer

    @property
    def consumers(self) -> tuple[OutboxConsumer, ...]:
        return tuple(self._consumers[name] for name in sorted(self._consumers))

    def for_event(self, event: dict[str, Any]) -> tuple[OutboxConsumer, ...]:
        return tuple(consumer for consumer in self.consumers if consumer.matches(event))

    def consumed_aggregate_types(self) -> frozenset[str]:
        return frozenset(kind for consumer in self.consumers for kind in consumer.aggregate_types)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "consumer": consumer.consumer_name,
                "aggregate_types": sorted(consumer.aggregate_types),
                "event_type_pattern": consumer.event_type_pattern,
            }
            for consumer in self.consumers
        ]


@dataclass
class RelayReport:
    claimed: int = 0
    published: int = 0
    retried: int = 0
    dead_lettered: int = 0
    deliveries: dict[str, int] = field(default_factory=dict)


def retry_delay_seconds(attempt: int) -> int:
    return min(MAX_RETRY_DELAY_SECONDS, 2 ** max(0, attempt))


class OutboxRelayWorker:
    def __init__(
        self,
        database: Any,
        registry: OutboxConsumerRegistry,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        lease_seconds: int = 60,
        max_batches: int = 50,
    ) -> None:
        self.database = database
        self.registry = registry
        self.batch_size = max(1, int(batch_size))
        self.max_attempts = max(1, int(max_attempts))
        self.lease_seconds = max(1, int(lease_seconds))
        self.max_batches = max(1, int(max_batches))

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        # The relay serves every workspace; outbox rows carry their own.
        with system_scope(SYSTEM_OPERATION), self.database.transaction() as connection:
            yield connection

    def run_once(self) -> RelayReport:
        """Drain pending events, up to ``max_batches`` batches."""
        report = RelayReport()
        for _ in range(self.max_batches):
            with self._transaction() as connection:
                batch = PostgresOutboxRepository(connection).claim_batch(
                    consumer_id=RELAY_ID, limit=self.batch_size, lease_seconds=self.lease_seconds,
                )
            report.claimed += len(batch)
            for event in batch:
                self._deliver(event, report)
            if len(batch) < self.batch_size:
                break
        if report.claimed:
            logger.info(
                "outbox_relay claimed=%s published=%s retried=%s dead_lettered=%s",
                report.claimed, report.published, report.retried, report.dead_lettered,
            )
        return report

    def _deliver(self, event: dict[str, Any], report: RelayReport) -> None:
        failed: tuple[str, str, str] | None = None  # (consumer, status, error)
        pending = False
        for consumer in self.registry.for_event(event):
            outcome = self._run_consumer(consumer, event)
            if outcome[0] == "busy":
                pending = True
            elif outcome[0] in {"failed", "dead_letter"}:
                failed = (consumer.consumer_name, outcome[0], outcome[1])
                if outcome[0] == "dead_letter":
                    break
            elif outcome[0] == "completed":
                report.deliveries[consumer.consumer_name] = report.deliveries.get(consumer.consumer_name, 0) + 1
        with self._transaction() as connection:
            outbox = PostgresOutboxRepository(connection)
            token = event["claim_token"]
            if failed is not None and failed[1] == "dead_letter":
                outbox.mark_dead_letter(
                    event_id=event["id"], claim_token=token, consumer_id=failed[0], reason=failed[2],
                )
                report.dead_lettered += 1
            elif failed is not None or pending:
                outbox.mark_retry(
                    event_id=event["id"],
                    claim_token=token,
                    error=failed[2] if failed is not None else "consumer busy",
                    retry_delay_seconds=retry_delay_seconds(event["attempt_count"]),
                )
                report.retried += 1
            elif outbox.mark_published(event_id=event["id"], claim_token=token):
                report.published += 1

    def _run_consumer(self, consumer: OutboxConsumer, event: dict[str, Any]) -> tuple[str, str]:
        name, key = consumer.consumer_name, event["event_key"]
        with self._transaction() as connection:
            reservation = PostgresOutboxConsumerRepository(connection).begin(
                consumer_id=name, event_key=key, lease_seconds=self.lease_seconds,
            )
        # A delivery already dead-lettered for this consumer (a retired module's, PA-4.3) is final.
        if reservation["state"] in {"duplicate_completed", "dead_lettered"}:
            return "duplicate", ""
        if reservation["state"] == "busy":
            return "busy", ""
        token = reservation["claim_token"]
        try:
            with self._transaction() as connection:
                result = consumer.handler(connection, event)
                inbox = PostgresOutboxConsumerRepository(connection)
                if not inbox.complete(consumer_id=name, event_key=key, claim_token=token, result=result or {}):
                    raise RuntimeError("consumer inbox lease expired before completion")
            return "completed", ""
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:2000]
            logger.warning("outbox_consumer_failed consumer=%s event=%s error=%s", name, key, error)
            with self._transaction() as connection:
                status = PostgresOutboxConsumerRepository(connection).fail(
                    consumer_id=name, event_key=key, claim_token=token, error=error,
                    max_attempts=self.max_attempts,
                )
            return status, error


def outbox_consumer_registry(features: Iterable[Any] = ()) -> OutboxConsumerRegistry:
    """Kernel consumers plus every feature's ``outbox_consumers``."""
    from app.events.run_streams import run_stream_consumer

    registry = OutboxConsumerRegistry([run_stream_consumer()])
    for feature in features:
        for consumer in getattr(feature, "outbox_consumers", ()):
            registry.register(consumer)
    return registry


__all__ = [
    "OutboxConsumer",
    "OutboxConsumerRegistry",
    "OutboxRelayWorker",
    "RelayReport",
    "outbox_consumer_registry",
    "retry_delay_seconds",
]
