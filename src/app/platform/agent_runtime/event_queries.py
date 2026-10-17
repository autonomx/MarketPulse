"""Typed reads of a run's event log (WP-7.4).

Callers used ``list_events(run_id, after_sequence=0, limit=5000)`` and then
searched the result, so a run past 5,000 events silently lost its later
events. These helpers read exactly what a caller needs (the latest event of a
type, or every event of some types) and page through the whole log when a
caller needs all of it.

Repositories without the typed queries (in-memory test doubles) fall back to
``list_events``.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .contracts import AgentEvent

_FALLBACK_LIMIT = 5000


def json_object(value: object) -> dict[str, Any]:
    """``value`` when it is a JSON object (an event payload field), else an empty one."""
    return value if isinstance(value, dict) else {}


def all_events(repository: Any, run_id: str) -> list[AgentEvent]:
    iterate = getattr(repository, "iter_events", None)
    if callable(iterate):
        return list(iterate(run_id))
    return list(repository.list_events(run_id, after_sequence=0, limit=_FALLBACK_LIMIT))


def events_of_types(repository: Any, run_id: str, event_types: Iterable[str]) -> list[AgentEvent]:
    types = set(event_types)
    iterate = getattr(repository, "iter_events", None)
    if callable(iterate):
        return list(iterate(run_id, event_types=types))
    return [
        event
        for event in repository.list_events(run_id, after_sequence=0, limit=_FALLBACK_LIMIT)
        if event.event_type in types
    ]


def latest_event(
    repository: Any,
    run_id: str,
    event_type: str,
    *,
    payload_contains: dict[str, Any] | None = None,
) -> AgentEvent | None:
    query = getattr(repository, "latest_event", None)
    if callable(query):
        return query(run_id, event_type, payload_contains=payload_contains)
    for event in reversed(repository.list_events(run_id, after_sequence=0, limit=_FALLBACK_LIMIT)):
        if event.event_type != event_type:
            continue
        if payload_contains and any(event.payload.get(key) != value for key, value in payload_contains.items()):
            continue
        return event
    return None


__all__ = ["all_events", "events_of_types", "latest_event"]
