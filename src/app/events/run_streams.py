"""Wake agent, task-graph and workflow run streams on new events (WP-5.3).

The ``run-streams`` outbox consumer sends ``NOTIFY omnix_run_events`` with
``<workspace>:<aggregate_type>:<run_id>`` when it delivers a run event. Each
process keeps one ``LISTEN`` connection (``RunEventWakeups``) and wakes the
SSE streams of that run, which then read the new rows. Streams keep a
fallback poll, so a missing relay or listener delays events but never loses
them.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from app.events.outbox_relay import OutboxConsumer

logger = logging.getLogger(__name__)

CHANNEL = "omnix_run_events"
RUN_AGGREGATE_TYPES = frozenset({"agent_run", "task_graph_run", "workflow_run"})
FALLBACK_POLL_SECONDS = 5.0
LISTEN_RETRY_SECONDS = 30.0


def wakeup_key(workspace_id: str, aggregate_type: str, run_id: str) -> str:
    return f"{workspace_id}:{aggregate_type}:{run_id}"


def _notify_run_stream(connection: Any, event: dict[str, Any]) -> dict[str, Any]:
    key = wakeup_key(event["workspace_id"], event["aggregate_type"], event["aggregate_id"])
    # Delivered when the consumer's transaction commits.
    connection.execute("SELECT pg_notify(%s, %s)", (CHANNEL, key))
    return {"notified": key}


def run_stream_consumer() -> OutboxConsumer:
    return OutboxConsumer(
        consumer_name="run-streams",
        aggregate_types=RUN_AGGREGATE_TYPES,
        handler=_notify_run_stream,
    )


class RunStreamSubscription:
    """A stream's wake-up flag; set by notifications until the stream reads."""

    def __init__(self, owner: "RunEventWakeups", key: str, loop: asyncio.AbstractEventLoop) -> None:
        self.owner = owner
        self.key = key
        self.loop = loop
        self.event = asyncio.Event()

    async def wait(self, timeout: float = FALLBACK_POLL_SECONDS) -> bool:
        """Wait for a notification since the last wait; ``False`` on timeout."""
        try:
            await asyncio.wait_for(self.event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self.event.clear()

    def close(self) -> None:
        self.owner.unsubscribe(self)

    def __enter__(self) -> "RunStreamSubscription":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class RunEventWakeups:
    """One ``LISTEN`` connection per process; subscriptions are woken by key."""

    def __init__(self, database_url: str | None) -> None:
        self.database_url = database_url
        self._subscriptions: dict[str, set[RunStreamSubscription]] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._failed_at: float | None = None

    def subscribe(self, key: str) -> RunStreamSubscription:
        """Subscribe before the first read so no notification falls between."""
        subscription = RunStreamSubscription(self, key, asyncio.get_running_loop())
        with self._lock:
            self._subscriptions.setdefault(key, set()).add(subscription)
            retry_due = self._failed_at is None or time.monotonic() - self._failed_at >= LISTEN_RETRY_SECONDS
            if self.database_url and self._thread is None and retry_due:
                self._thread = threading.Thread(target=self._listen, name="omnix-run-event-listener", daemon=True)
                self._thread.start()
        return subscription

    def unsubscribe(self, subscription: RunStreamSubscription) -> None:
        with self._lock:
            subscriptions = self._subscriptions.get(subscription.key)
            if subscriptions is not None:
                subscriptions.discard(subscription)
                if not subscriptions:
                    del self._subscriptions[subscription.key]

    def wake(self, key: str) -> None:
        with self._lock:
            subscriptions = tuple(self._subscriptions.get(key, ()))
        for subscription in subscriptions:
            try:
                subscription.loop.call_soon_threadsafe(subscription.event.set)
            except RuntimeError:  # the stream's loop has closed
                continue

    def _listen(self) -> None:
        try:
            import psycopg

            with psycopg.connect(str(self.database_url), autocommit=True) as connection:
                connection.execute(f"LISTEN {CHANNEL}")
                while True:
                    for notice in connection.notifies(timeout=1.0, stop_after=100):
                        self.wake(notice.payload)
                    with self._lock:
                        # Decided under the lock: a new subscriber either sees
                        # this thread running or starts the next one.
                        if not self._subscriptions:
                            self._thread = None
                            return
        except Exception:
            logger.warning("run_event_listen_unavailable; run streams poll every %ss", FALLBACK_POLL_SECONDS)
            with self._lock:
                self._thread = None
                self._failed_at = time.monotonic()


_WAKEUPS: RunEventWakeups | None = None
_WAKEUPS_LOCK = threading.Lock()


def run_event_wakeups() -> RunEventWakeups:
    """The process's wakeups, listening on the runtime database."""
    global _WAKEUPS
    with _WAKEUPS_LOCK:
        if _WAKEUPS is None:
            url: str | None
            try:
                from app.persistence.config import database_settings

                url = database_settings().url
            except Exception:
                url = None
            _WAKEUPS = RunEventWakeups(url)
        return _WAKEUPS


__all__ = [
    "CHANNEL",
    "FALLBACK_POLL_SECONDS",
    "RUN_AGGREGATE_TYPES",
    "RunEventWakeups",
    "RunStreamSubscription",
    "run_event_wakeups",
    "run_stream_consumer",
    "wakeup_key",
]
