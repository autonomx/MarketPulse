"""Global limit on concurrently running agent processes (WP-4.7).

``OMNIX_AGENT_MAX_CONCURRENT_RUNS`` (default 2) agent processes may run at once
across every Omnix process. A run takes a slot before its agent process starts:
under the singleton lock row, expired slots are cleared, the live ones counted
and, below the limit, a slot is inserted with a lease. The holder renews the
lease while the agent runs and deletes the slot when it exits; a holder that
dies leaves a slot that expires after the lease.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field

from app.config.env import env_int
from app.persistence.database import PostgresDatabase, default_database

_LOG = logging.getLogger(__name__)
DEFAULT_MAX_CONCURRENT_RUNS = 2
DEFAULT_LEASE_SECONDS = 120
DEFAULT_WAIT_SECONDS = 300


class AgentRunCapacityError(RuntimeError):
    """No agent run slot freed up before the deadline."""


def max_concurrent_runs() -> int:
    return env_int("OMNIX_AGENT_MAX_CONCURRENT_RUNS", DEFAULT_MAX_CONCURRENT_RUNS, minimum=1)


def slot_wait_seconds() -> float:
    return float(env_int("OMNIX_AGENT_SLOT_WAIT_SECONDS", int(DEFAULT_WAIT_SECONDS), minimum=0))


class PostgresAgentRunSlots:
    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        max_runs: int | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.database = database or default_database()
        self.max_runs = max_runs if max_runs is not None else max_concurrent_runs()
        self.lease_seconds = max(10, int(lease_seconds))

    def try_acquire(self, run_id: str, holder: str) -> bool:
        with self.database.transaction() as connection:
            connection.execute("SELECT singleton FROM omnix_agent_run_slot_lock WHERE singleton FOR UPDATE")
            connection.execute("DELETE FROM omnix_agent_run_slots WHERE lease_expires_at <= CURRENT_TIMESTAMP")
            renewed = connection.execute(
                "UPDATE omnix_agent_run_slots SET holder = %s, "
                "lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second') "
                "WHERE run_id = %s RETURNING run_id",
                (holder, self.lease_seconds, run_id),
            ).fetchone()
            if renewed is not None:
                return True  # the same run restarted its agent
            active = int(connection.execute("SELECT COUNT(*) FROM omnix_agent_run_slots").fetchone()[0])
            if active >= self.max_runs:
                return False
            connection.execute(
                "INSERT INTO omnix_agent_run_slots (run_id, holder, lease_expires_at) "
                "VALUES (%s, %s, CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'))",
                (run_id, holder, self.lease_seconds),
            )
            return True

    def acquire(self, run_id: str, *, wait_seconds: float | None = None, poll_seconds: float = 2.0) -> AgentRunSlot:
        holder = f"{socket.gethostname()}:{os.getpid()}"
        deadline = time.monotonic() + (slot_wait_seconds() if wait_seconds is None else wait_seconds)
        while True:
            if self.try_acquire(run_id, holder):
                return AgentRunSlot(self, run_id, holder)
            if time.monotonic() >= deadline:
                raise AgentRunCapacityError(
                    f"{self.max_runs} agent runs are already running; this run waited for a free slot and "
                    "gave up (OMNIX_AGENT_MAX_CONCURRENT_RUNS)"
                )
            time.sleep(poll_seconds)

    def renew(self, run_id: str, holder: str) -> bool:
        with self.database.transaction() as connection:
            row = connection.execute(
                "UPDATE omnix_agent_run_slots SET lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second') "
                "WHERE run_id = %s AND holder = %s RETURNING run_id",
                (self.lease_seconds, run_id, holder),
            ).fetchone()
        return row is not None

    def release(self, run_id: str, holder: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM omnix_agent_run_slots WHERE run_id = %s AND holder = %s", (run_id, holder))

    def active(self) -> int:
        with self.database.transaction() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM omnix_agent_run_slots WHERE lease_expires_at > CURRENT_TIMESTAMP"
            ).fetchone()[0])


@dataclass
class AgentRunSlot:
    """A held slot; renewed in the background until released."""

    slots: PostgresAgentRunSlots
    run_id: str
    holder: str
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def __post_init__(self) -> None:
        self._thread = threading.Thread(target=self._renew_loop, name=f"agent-slot-{self.run_id[:8]}", daemon=True)
        self._thread.start()

    def _renew_loop(self) -> None:
        interval = max(5.0, self.slots.lease_seconds / 3)
        while not self._stop.wait(interval):
            try:
                if not self.slots.renew(self.run_id, self.holder):
                    _LOG.warning("agent_run_slot_lost run_id=%s", self.run_id)
                    return
            except Exception:  # noqa: BLE001 - the lease covers a missed renewal
                _LOG.warning("agent_run_slot_renew_failed run_id=%s", self.run_id, exc_info=True)

    def release(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self.slots.release(self.run_id, self.holder)
        except Exception:  # noqa: BLE001 - the lease expires anyway
            _LOG.warning("agent_run_slot_release_failed run_id=%s", self.run_id, exc_info=True)


def default_agent_run_slots() -> PostgresAgentRunSlots | None:
    from app.persistence.runtime import uses_postgresql_runtime

    return PostgresAgentRunSlots() if uses_postgresql_runtime() else None


__all__ = [
    "AgentRunCapacityError",
    "AgentRunSlot",
    "PostgresAgentRunSlots",
    "default_agent_run_slots",
    "max_concurrent_runs",
]
