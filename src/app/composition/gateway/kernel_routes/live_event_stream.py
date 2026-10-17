"""Resilient live job-event streaming for the runtime gateway."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.persistence.database import PostgresOperationError

EVENT_STREAM_BATCH_LIMIT = 100
EVENT_STREAM_POLL_SECONDS = 1.0
EVENT_STREAM_HEARTBEAT_SECONDS = 15.0


def _sse_event(event_type: str, payload: dict[str, Any], event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(payload, sort_keys=True)}")
    return "\n".join(lines) + "\n\n"


def _sse_comment(comment: str) -> str:
    return f": {comment}\n\n"


def _parse_event_id(value: str | None, fallback: int = 0) -> int:
    if not value:
        return fallback
    try:
        return max(0, int(value))
    except ValueError:
        return fallback


def legacy_event_id(value: str | None) -> int | None:
    """The id part of a cursor, for stores without commit-order cursors."""
    if value is None or value == "":
        return None
    return _parse_event_id(value.rsplit(":", 1)[-1])


def latest_job_event_id(job_store: Any) -> int:
    """Return the current event tail without replaying the complete event table."""

    latest = getattr(job_store, "latest_event_id", None)
    if callable(latest):
        try:
            return max(0, int(latest()))
        except (TypeError, ValueError, PostgresOperationError, OSError):
            return 0
    return 0


def live_event_start_id(
    job_store: Any,
    *,
    after_id: int | None,
    last_event_id: str | None,
) -> int:
    """Resume explicit cursors, otherwise subscribe at the current event tail."""

    if last_event_id is not None:
        return _parse_event_id(last_event_id)
    if after_id is not None:
        return max(0, after_id)
    return latest_job_event_id(job_store)


async def resilient_live_job_event_stream(job_store: Any, after_id: int = 0):
    last_event_id = max(0, after_id)
    seconds_until_heartbeat = 0.0
    yield _sse_comment("omnix-events-open")
    while True:
        try:
            events = await asyncio.to_thread(job_store.list_events,
                after_id=last_event_id,
                limit=EVENT_STREAM_BATCH_LIMIT,
            )
        except (PostgresOperationError, OSError):
            yield _sse_comment("event-store-temporarily-unavailable")
            await asyncio.sleep(EVENT_STREAM_POLL_SECONDS)
            continue

        if events:
            for event in events:
                last_event_id = max(last_event_id, event.id)
                yield _sse_event(
                    event.event_type,
                    event.model_dump(mode="json"),
                    event_id=event.id,
                )
            seconds_until_heartbeat = 0.0
            continue

        if seconds_until_heartbeat <= 0:
            yield _sse_comment("heartbeat")
            seconds_until_heartbeat = EVENT_STREAM_HEARTBEAT_SECONDS
        await asyncio.sleep(EVENT_STREAM_POLL_SECONDS)
        seconds_until_heartbeat -= EVENT_STREAM_POLL_SECONDS


def _committed_event(event: dict[str, Any]) -> str:
    from app.events.event_reader import event_cursor

    payload = {key: value for key, value in event.items() if key != "tx_id"}
    lines = [f"id: {event_cursor(event)}", f"event: {event['event_type']}", f"data: {json.dumps(payload, sort_keys=True)}"]
    return "\n".join(lines) + "\n\n"


async def committed_event_stream(reader: Any, start: Any = None):
    """SSE from the process's shared reader (WP-5.4).

    Replays from ``start`` (the client's ``Last-Event-ID``) in commit order,
    then follows the live queue. Replays longer than ``REPLAY_LIMIT`` and
    subscribers that fall behind get ``event: resync`` and are closed; the
    client refetches state and reconnects with its cursor.
    """
    from app.events.event_reader import REPLAY_LIMIT, event_cursor
    from app.observability.metrics import record_sse_delivered, record_sse_resync, sse_subscriber

    loop = asyncio.get_running_loop()
    subscription, live_from = await asyncio.to_thread(reader.subscribe, loop)
    try:
        with sse_subscriber("jobs"):
            yield _sse_comment("omnix-events-open")
            last = start if start is not None else live_from
            replayed = 0
            while True:
                batch = await asyncio.to_thread(reader.events_after, last)
                for event in batch:
                    replayed += 1
                    if replayed > REPLAY_LIMIT:
                        record_sse_resync("jobs", "replay_limit")
                        yield "event: resync\ndata: {}\n\n"
                        return
                    yield _committed_event(event)
                    record_sse_delivered("jobs", "replay")
                    last = event_cursor(event)
                if len(batch) < 500:
                    break
            while True:
                try:
                    event = await asyncio.wait_for(subscription.queue.get(), timeout=EVENT_STREAM_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield _sse_comment("heartbeat")
                    continue
                if subscription.overflowed:
                    record_sse_resync("jobs", "overflow")
                    yield "event: resync\ndata: {}\n\n"
                    return
                cursor = event_cursor(event)
                if cursor <= last:
                    continue
                yield _committed_event(event)
                record_sse_delivered("jobs", "live")
                last = cursor
    finally:
        reader.unsubscribe(subscription)
