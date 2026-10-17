"""Module lifecycle state in PostgreSQL (ADR-0016, PA-4.3).

A module is ``active`` (the default, which defers to the runtime
configuration), ``draining`` (being retired: it takes no new work, while its
handlers finish what is in flight) or ``retired``. The database state is
authoritative and read by every process: a process whose configuration enables
a draining module still refuses new work for it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.caching.bounded_cache import bounded_lru_cache

logger = logging.getLogger(__name__)

State = Literal["active", "draining", "retired"]
STATES: frozenset[str] = frozenset({"active", "draining", "retired"})
# How often a process re-reads a module's state for its routes and scheduled tasks.
STATE_CACHE_SECONDS = 5.0


@dataclass(frozen=True)
class ModuleState:
    module_id: str
    state: State = "active"
    drain_deadline: datetime | None = None

    @property
    def accepts_new_work(self) -> bool:
        return self.state == "active"

    def accepts_follow_up(self, now: datetime | None = None) -> bool:
        """A draining module still takes work its own running jobs submit, until the drain deadline."""
        if self.state != "draining":
            return self.state == "active"
        return self.drain_deadline is None or (now or datetime.now(timezone.utc)) < self.drain_deadline


def read_module_state(connection: Any, module_id: str) -> ModuleState:
    row = connection.execute(
        "SELECT state, drain_deadline FROM omnix_module_states WHERE module_id = %s", (module_id,),
    ).fetchone()
    if row is None:
        return ModuleState(module_id)
    return ModuleState(module_id, state=row[0], drain_deadline=row[1])


def set_module_state(
    connection: Any, module_id: str, state: State, *, drain_deadline: datetime | None = None, reason: str = "",
) -> ModuleState:
    if state not in STATES:
        raise ValueError(f"unknown module state {state!r}")
    connection.execute(
        """INSERT INTO omnix_module_states (module_id, state, drain_deadline, reason)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (module_id) DO UPDATE SET state = EXCLUDED.state,
               drain_deadline = EXCLUDED.drain_deadline, reason = EXCLUDED.reason,
               updated_at = CURRENT_TIMESTAMP""",
        (module_id, state, drain_deadline, reason),
    )
    cached_module_state.cache_clear()
    return ModuleState(module_id, state=state, drain_deadline=drain_deadline)


@bounded_lru_cache(max_entries=64, ttl_seconds=STATE_CACHE_SECONDS)
def cached_module_state(module_id: str) -> ModuleState:
    """The state as of at most a few seconds ago, for routes and schedulers.

    Without a reachable PostgreSQL authority (an in-memory test runtime) a
    module counts as active; job creation re-reads the state in its own
    transaction, which is the authoritative check.
    """
    try:
        from .database import default_database

        with default_database().connection() as connection:
            return read_module_state(connection, module_id)
    except Exception as exc:  # noqa: BLE001 - availability: an unreadable state never blocks a route
        logger.debug("module state unavailable for %s: %s", module_id, exc)
        return ModuleState(module_id)


class ModuleNotAcceptingWork(RuntimeError):
    """The module is draining or retired; the request may be retried elsewhere or later."""

    def __init__(self, state: ModuleState) -> None:
        super().__init__(f"module {state.module_id} is {state.state}")
        self.state = state
