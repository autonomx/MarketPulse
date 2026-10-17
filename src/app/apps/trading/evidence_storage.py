"""Storage for market evidence: PostgreSQL in production, memory without a database (WP-8.3).

Trading decision inputs are not read from local files. Yahoo 1-minute bars are
kept as revisions identified by their content (everything except
``received_at``): seeing the same bar again adds nothing, and the earliest
receipt is what causal replay may know. Evidence counters are incremented
atomically. Market evidence is shared by every workspace.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from app.persistence.unit_of_work import PostgresUnitOfWork, unit_of_work

_BAR_FIELDS = ("start_time", "end_time", "open", "high", "low", "close", "volume", "session", "provider_event_id")


def _normalized(record: dict[str, Any]) -> dict[str, Any]:
    """One canonical form per bar, so the same bar always hashes alike."""

    def stamp(value: Any) -> str:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return parsed.astimezone(timezone.utc).isoformat()

    def number(value: Any) -> str:
        return format(Decimal(str(value)).normalize(), "f")

    return {
        "start_time": stamp(record["start_time"]),
        "end_time": stamp(record["end_time"]),
        "open": number(record["open"]),
        "high": number(record["high"]),
        "low": number(record["low"]),
        "close": number(record["close"]),
        "volume": number(record.get("volume") or "0"),
        "session": str(record.get("session") or "regular"),
        "provider_event_id": None if record.get("provider_event_id") is None else str(record["provider_event_id"]),
        "received_at": stamp(record["received_at"]),
    }


def content_hash(record: dict[str, Any]) -> str:
    """The revision identity: every field except when it was received."""
    canonical = "|".join(str(record[name]) for name in _BAR_FIELDS)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def distinct_revisions(records: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Normalized records keyed by (start time, content), keeping the earliest receipt."""
    distinct: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in records:
        record = _normalized(raw)
        key = (record["start_time"], content_hash(record))
        known = distinct.get(key)
        if known is None or record["received_at"] < known["received_at"]:
            distinct[key] = record
    return distinct


class YahooEvidenceBackend(Protocol):
    name: str
    persistent: bool

    def add_revisions(self, symbol: str, session_date: date, records: list[dict[str, Any]]) -> int: ...
    def revisions(self, symbol: str, session_date: date) -> list[dict[str, Any]]: ...
    def add_counters(self, provider: str, deltas: dict[str, int]) -> None: ...
    def counters(self, provider: str) -> dict[str, int]: ...
    def add_session_counters(self, provider: str, session_date: date, deltas: dict[str, int]) -> None: ...
    def session_counters(self, provider: str, session_date: date) -> tuple[dict[str, int], datetime | None]: ...


class MemoryEvidenceBackend:
    """Process memory, for tests and processes without a database."""

    name = "memory"
    persistent = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bars: dict[tuple[str, date], dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
        self._counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._sessions: dict[tuple[str, date], dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._session_updated: dict[tuple[str, date], datetime] = {}

    def add_revisions(self, symbol, session_date, records):
        written = 0
        with self._lock:
            stored = self._bars[(symbol, session_date)]
            for key, record in distinct_revisions(records).items():
                known = stored.get(key)
                if known is None:
                    stored[key] = record
                    written += 1
                elif record["received_at"] < known["received_at"]:
                    stored[key] = record
        return written

    def revisions(self, symbol, session_date):
        with self._lock:
            return [dict(record) for record in self._bars.get((symbol, session_date), {}).values()]

    def add_counters(self, provider, deltas):
        with self._lock:
            for name, delta in deltas.items():
                self._counters[provider][name] += int(delta)

    def counters(self, provider):
        with self._lock:
            return dict(self._counters.get(provider, {}))

    def add_session_counters(self, provider, session_date, deltas):
        with self._lock:
            for name, delta in deltas.items():
                self._sessions[(provider, session_date)][name] += int(delta)
            self._session_updated[(provider, session_date)] = datetime.now(timezone.utc)

    def session_counters(self, provider, session_date):
        with self._lock:
            return dict(self._sessions.get((provider, session_date), {})), self._session_updated.get((provider, session_date))


class PostgresEvidenceBackend:
    name = "postgresql"
    persistent = True

    def __init__(
        self,
        *,
        uow_factory: Callable[[], AbstractContextManager[PostgresUnitOfWork]] = unit_of_work,
    ) -> None:
        self.uow_factory = uow_factory

    def add_revisions(self, symbol, session_date, records):
        distinct = list(distinct_revisions(records).items())
        if not distinct:
            return 0
        columns: dict[str, list[Any]] = {name: [] for name in ("start_time", "content_sha256", "end_time", "open", "high", "low", "close",
                                         "volume", "session", "provider_event_id", "received_at")}
        for (start, digest), record in distinct:
            columns["start_time"].append(start)
            columns["content_sha256"].append(digest)
            for name in ("end_time", "open", "high", "low", "close", "volume", "session", "provider_event_id", "received_at"):
                columns[name].append(record[name])
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                INSERT INTO omnix_trading_yahoo_bar_revisions AS stored (
                    symbol, session_date, start_time, content_sha256, end_time,
                    open, high, low, close, volume, session, provider_event_id, received_at
                )
                SELECT %s, %s, incoming.*
                  FROM unnest(
                      %s::timestamptz[], %s::text[], %s::timestamptz[],
                      %s::numeric[], %s::numeric[], %s::numeric[], %s::numeric[], %s::numeric[],
                      %s::text[], %s::text[], %s::timestamptz[]
                  ) AS incoming
                ON CONFLICT (symbol, start_time, content_sha256) DO UPDATE
                   SET received_at = EXCLUDED.received_at
                 WHERE EXCLUDED.received_at < stored.received_at
                RETURNING (xmax = 0)
                """,
                (
                    symbol, session_date,
                    columns["start_time"], columns["content_sha256"], columns["end_time"],
                    columns["open"], columns["high"], columns["low"], columns["close"], columns["volume"],
                    columns["session"], columns["provider_event_id"], columns["received_at"],
                ),
            ).fetchall()
            uow.commit()
        return sum(1 for row in rows if row[0])

    def revisions(self, symbol, session_date):
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT start_time, end_time, open, high, low, close, volume, session, provider_event_id, received_at
                  FROM omnix_trading_yahoo_bar_revisions
                 WHERE symbol = %s AND session_date = %s
                 ORDER BY start_time, received_at, provider_event_id
                """,
                (symbol, session_date),
            ).fetchall()
        return [
            {
                "start_time": row[0].astimezone(timezone.utc).isoformat(),
                "end_time": row[1].astimezone(timezone.utc).isoformat(),
                "open": str(row[2]),
                "high": str(row[3]),
                "low": str(row[4]),
                "close": str(row[5]),
                "volume": str(row[6]),
                "session": row[7],
                "provider_event_id": row[8],
                "received_at": row[9].astimezone(timezone.utc).isoformat(),
            }
            for row in rows
        ]

    def add_counters(self, provider, deltas):
        self._add(provider, None, deltas)

    def counters(self, provider):
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                "SELECT name, value FROM omnix_trading_evidence_counters WHERE provider = %s",
                (provider,),
            ).fetchall()
        return {str(name): int(value) for name, value in rows}

    def add_session_counters(self, provider, session_date, deltas):
        self._add(provider, session_date, deltas)

    def session_counters(self, provider, session_date):
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT name, value, updated_at
                  FROM omnix_trading_evidence_session_counters
                 WHERE provider = %s AND session_date = %s
                """,
                (provider, session_date),
            ).fetchall()
        updated = max((row[2] for row in rows), default=None)
        return {str(row[0]): int(row[1]) for row in rows}, updated

    def _add(self, provider: str, session_date: date | None, deltas: dict[str, int]) -> None:
        names = [name for name, delta in deltas.items() if int(delta)]
        if not names:
            return
        values = [int(deltas[name]) for name in names]
        with self.uow_factory() as uow:
            if session_date is None:
                uow.connection.execute(
                    """
                    INSERT INTO omnix_trading_evidence_counters AS stored (provider, name, value)
                    SELECT %s, name, value FROM unnest(%s::text[], %s::bigint[]) AS incoming(name, value)
                    ON CONFLICT (provider, name) DO UPDATE SET value = stored.value + EXCLUDED.value
                    """,
                    (provider, names, values),
                )
            else:
                uow.connection.execute(
                    """
                    INSERT INTO omnix_trading_evidence_session_counters AS stored (provider, session_date, name, value)
                    SELECT %s, %s, name, value FROM unnest(%s::text[], %s::bigint[]) AS incoming(name, value)
                    ON CONFLICT (provider, session_date, name) DO UPDATE
                       SET value = stored.value + EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
                    """,
                    (provider, session_date, names, values),
                )
            uow.commit()


def _postgresql_configured() -> bool:
    """The process runs on PostgreSQL and has a database to reach."""
    from app.config.env import env_str
    from app.persistence.runtime import uses_postgresql_runtime

    return uses_postgresql_runtime() and bool((env_str("OMNIX_DATABASE_URL", "") or "").strip())


def default_evidence_backend() -> YahooEvidenceBackend:
    """PostgreSQL when the process has a database, otherwise memory (never local files)."""
    return PostgresEvidenceBackend() if _postgresql_configured() else MemoryEvidenceBackend()


class IbkrSessionEvidence(Protocol):
    persistent: bool

    def read(self, session_date: date) -> dict[str, Any] | None: ...
    def apply(self, session_date: date, empty: dict[str, Any], mutators: list[Callable[[dict[str, Any]], None]]) -> None: ...


class MemoryIbkrSessionEvidence:
    persistent = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[date, dict[str, Any]] = {}

    def read(self, session_date):
        with self._lock:
            payload = self._sessions.get(session_date)
            return json.loads(json.dumps(payload)) if payload is not None else None

    def apply(self, session_date, empty, mutators):
        with self._lock:
            payload = self._sessions.get(session_date) or json.loads(json.dumps(empty))
            for mutate in mutators:
                mutate(payload)
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._sessions[session_date] = payload


class PostgresIbkrSessionEvidence:
    persistent = True

    def __init__(
        self,
        *,
        uow_factory: Callable[[], AbstractContextManager[PostgresUnitOfWork]] = unit_of_work,
    ) -> None:
        self.uow_factory = uow_factory

    def read(self, session_date):
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                "SELECT payload FROM omnix_trading_ibkr_session_evidence WHERE session_date = %s",
                (session_date,),
            ).fetchone()
        return dict(row[0]) if row is not None else None

    def apply(self, session_date, empty, mutators):
        """Apply queued mutations to the session's diagnostics under its row lock."""
        with self.uow_factory() as uow:
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_ibkr_session_evidence (session_date, payload)
                VALUES (%s, %s::jsonb)
                ON CONFLICT (session_date) DO NOTHING
                """,
                (session_date, json.dumps(empty)),
            )
            row = uow.connection.execute(
                "SELECT payload FROM omnix_trading_ibkr_session_evidence WHERE session_date = %s FOR UPDATE",
                (session_date,),
            ).fetchone()
            payload = {**empty, **dict(row[0])}
            for mutate in mutators:
                mutate(payload)
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            uow.connection.execute(
                """
                UPDATE omnix_trading_ibkr_session_evidence
                   SET payload = %s::jsonb, updated_at = CURRENT_TIMESTAMP
                 WHERE session_date = %s
                """,
                (json.dumps(payload, sort_keys=True), session_date),
            )
            uow.commit()


def default_ibkr_session_evidence() -> IbkrSessionEvidence:
    return PostgresIbkrSessionEvidence() if _postgresql_configured() else MemoryIbkrSessionEvidence()
