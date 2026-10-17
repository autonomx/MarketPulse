"""One job-event reader per process and workspace (WP-5.4).

Before, every open ``/events`` stream polled PostgreSQL once a second with an
id cursor, so the query rate grew with subscribers, and an event that
committed late with a lower id could be skipped.

Now one ``EventReader`` per workspace:

- waits on ``LISTEN omnix_events`` (the event writer notifies on commit),
  with a fallback poll every ``poll_seconds``;
- reads in commit order through a ``(tx_id, id)`` cursor that never passes
  a transaction still in progress;
- fans out to subscribers through bounded queues. A subscriber whose queue
  fills is marked overflowed and closed; its client reconnects with its
  cursor and replays.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from app.runtime.tenant_context import TenantContext, pop_tenant, push_tenant

logger = logging.getLogger(__name__)

SUBSCRIBER_QUEUE_SIZE = 1000
READ_BATCH = 500
REPLAY_LIMIT = 5000
CHANNEL = "omnix_events"


@dataclass(frozen=True, order=True, slots=True)
class EventCursor:
    tx_id: int
    event_id: int

    def __str__(self) -> str:
        return f"{self.tx_id}:{self.event_id}"

    @classmethod
    def parse(cls, value: str | int | None) -> "EventCursor | None":
        """``"tx:id"``; a legacy integer id restarts conservatively from ``(0, id)``."""
        if value is None or value == "":
            return None
        text = str(value).strip()
        try:
            if ":" in text:
                tx_text, id_text = text.split(":", 1)
                return cls(max(0, int(tx_text)), max(0, int(id_text)))
            return cls(0, max(0, int(text)))
        except ValueError:
            return None


@dataclass(eq=False)
class Subscription:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE))
    overflowed: bool = False

    def offer(self, event: dict[str, Any]) -> None:
        """Called on the reader thread; never blocks it."""
        def put() -> None:
            if self.overflowed:
                return
            try:
                self.queue.put_nowait(event)
            except asyncio.QueueFull:
                # The consumer sees the flag on its next read and resyncs.
                self.overflowed = True

        self.loop.call_soon_threadsafe(put)


def event_cursor(event: dict[str, Any]) -> EventCursor:
    return EventCursor(int(event["tx_id"]), int(event["id"]))


class EventReader:
    """Reads one workspace's job events and fans them out."""

    def __init__(self, database: Any, tenant: TenantContext, *, poll_seconds: float = 5.0) -> None:
        self.database = database
        self.tenant = tenant
        self.poll_seconds = poll_seconds
        self.queries = 0
        self._subscribers: set[Subscription] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener: threading.Thread | None = None
        self.cursor = EventCursor(0, 0)

    # Reads ---------------------------------------------------------------

    def _repository_call(self, method: str, **kwargs: Any) -> Any:
        from app.persistence.job_repository import PostgresJobRepository

        token = push_tenant(self.tenant)
        try:
            with self.database.connection() as connection:
                self.queries += 1
                result = getattr(PostgresJobRepository(connection), method)(self.tenant, **kwargs)
                connection.rollback()
                return result
        finally:
            pop_tenant(token)

    def latest_cursor(self) -> EventCursor:
        return EventCursor(*self._repository_call("latest_committed_event_cursor"))

    def events_after(self, cursor: EventCursor, *, limit: int = READ_BATCH) -> list[dict[str, Any]]:
        return self._repository_call(
            "list_committed_events", after=(cursor.tx_id, cursor.event_id), limit=limit,
        )

    # Subscribers ---------------------------------------------------------

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> tuple[Subscription, EventCursor]:
        """A live subscription and the cursor it starts after."""
        subscription = Subscription(loop=loop)
        with self._lock:
            self._subscribers.add(subscription)
            self._ensure_running()
            return subscription, self.cursor

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            self._subscribers.discard(subscription)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # Loop ----------------------------------------------------------------

    def _ensure_running(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.cursor = self.latest_cursor()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"omnix-event-reader-{self.tenant.workspace_id}", daemon=True)
        self._thread.start()
        self._listener = threading.Thread(target=self._listen, name="omnix-event-listener", daemon=True)
        self._listener.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def poll_once(self) -> int:
        """Read and fan out everything committed since the cursor."""
        delivered = 0
        while not self._stop.is_set():
            events = self.events_after(self.cursor)
            if not events:
                break
            with self._lock:
                subscribers = list(self._subscribers)
            for event in events:
                for subscription in subscribers:
                    subscription.offer(event)
            self.cursor = event_cursor(events[-1])
            delivered += len(events)
            if len(events) < READ_BATCH:
                break
        return delivered

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=self.poll_seconds)
            self._wake.clear()
            if self._stop.is_set():
                break
            if not self.subscriber_count:
                continue
            try:
                self.poll_once()
            except Exception:
                logger.exception("event_reader_poll_failed workspace=%s", self.tenant.workspace_id)

    def _listen(self) -> None:
        """Wake the reader on NOTIFY; the poll keeps working without it."""
        try:
            import psycopg

            with psycopg.connect(self.database.settings.url, autocommit=True) as connection:
                connection.execute(f"LISTEN {CHANNEL}")
                while not self._stop.is_set():
                    for notice in connection.notifies(timeout=1.0, stop_after=50):
                        if notice.payload in {"", self.tenant.workspace_id}:
                            self._wake.set()
        except Exception:
            logger.warning("event_reader_listen_unavailable workspace=%s; polling only", self.tenant.workspace_id)


class EventReaders:
    """The process's readers, one per workspace (kept on ``app.state``)."""

    def __init__(self, database: Any, *, poll_seconds: float = 5.0) -> None:
        self.database = database
        self.poll_seconds = poll_seconds
        self._readers: dict[str, EventReader] = {}
        self._lock = threading.Lock()

    def for_tenant(self, tenant: TenantContext) -> EventReader:
        with self._lock:
            reader = self._readers.get(tenant.workspace_id)
            if reader is None:
                reader = EventReader(self.database, tenant, poll_seconds=self.poll_seconds)
                self._readers[tenant.workspace_id] = reader
            return reader

    def stop(self) -> None:
        with self._lock:
            for reader in self._readers.values():
                reader.stop()

    def diagnostics(self) -> dict[str, int]:
        """Readers, open subscriptions, queries and live NOTIFY listeners in this process."""
        with self._lock:
            readers = list(self._readers.values())
        return {
            "readers": len(readers),
            "subscribers": sum(reader.subscriber_count for reader in readers),
            "queries": sum(reader.queries for reader in readers),
            "listeners_alive": sum(1 for reader in readers if reader._listener is not None and reader._listener.is_alive()),
        }


__all__ = ["EventCursor", "EventReader", "EventReaders", "REPLAY_LIMIT", "Subscription", "event_cursor"]
