"""Session-scoped initiative lease authority with TTL and interruption semantics."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol, cast

from pydantic import Field

from app.persistence.database import PostgresDatabase, default_database

from .contracts import FrozenContract

InitiativeChannel = Literal["text", "avatar", "voice", "notification"]
InitiativeUrgency = Literal["low", "normal", "high", "critical"]
InitiativeInterruptibility = Literal[
    "never",
    "idle_only",
    "floor_available",
    "interrupt",
]
InitiativeDecisionReason = Literal[
    "initiative_acquired",
    "initiative_active",
    "initiative_spacing",
    "stale_generation",
    "preempted_by_critical_interrupt",
]

_URGENCY_RANK: dict[InitiativeUrgency, int] = {
    "low": 0,
    "normal": 1,
    "high": 2,
    "critical": 3,
}


class InitiativeLease(FrozenContract):
    lease_id: str = Field(min_length=1, max_length=240)
    session_id: str = Field(min_length=1, max_length=200)
    generation: str = Field(min_length=1, max_length=160)
    owner: str = Field(min_length=1, max_length=120)
    intent_id: str = Field(min_length=1, max_length=240)
    channel: InitiativeChannel
    urgency: InitiativeUrgency
    interruptibility: InitiativeInterruptibility
    acquired_at: datetime
    expires_at: datetime


class InitiativeAcquireRequest(FrozenContract):
    session_id: str = Field(min_length=1, max_length=200)
    generation: str = Field(min_length=1, max_length=160)
    owner: str = Field(min_length=1, max_length=120)
    intent_id: str = Field(min_length=1, max_length=240)
    channel: InitiativeChannel
    urgency: InitiativeUrgency = "normal"
    interruptibility: InitiativeInterruptibility = "idle_only"
    requested_at: datetime
    ttl_seconds: float = Field(default=15.0, gt=0.0, le=120.0)
    minimum_spacing_seconds: float = Field(default=25.0, ge=0.0, le=600.0)


class InitiativeDecision(FrozenContract):
    accepted: bool
    reason: InitiativeDecisionReason
    lease: InitiativeLease | None = None
    eligible_in_ms: int = Field(default=0, ge=0)


class InitiativeSnapshot(FrozenContract):
    session_id: str
    generation: str | None = None
    active_lease: InitiativeLease | None = None
    last_delivered_at: datetime | None = None
    last_delivered_owner: str | None = None
    consecutive_deliveries_by_owner: int = Field(default=0, ge=0)


class CompanionInitiativeAuthorityStore(Protocol):
    def register_generation(self, session_id: str, generation: str) -> InitiativeSnapshot: ...

    def acquire(self, request: InitiativeAcquireRequest) -> InitiativeDecision: ...

    def authorizes(self, lease: InitiativeLease, *, now: datetime) -> bool: ...

    def bind_intent(
        self,
        *,
        session_id: str,
        lease_id: str,
        intent_id: str,
        bound_at: datetime,
    ) -> InitiativeLease | None: ...

    def finish(
        self,
        *,
        session_id: str,
        lease_id: str,
        finished_at: datetime,
        delivered: bool,
    ) -> bool: ...

    def finish_intent(
        self,
        *,
        session_id: str,
        intent_id: str,
        finished_at: datetime,
        delivered: bool,
    ) -> bool: ...

    def snapshot(self, session_id: str, *, now: datetime | None = None) -> InitiativeSnapshot: ...

    def reset(self, session_id: str) -> None: ...


@dataclass(slots=True)
class _SessionInitiativeState:
    generation: str | None = None
    active: InitiativeLease | None = None
    last_delivered_at: datetime | None = None
    last_delivered_owner: str | None = None
    consecutive_deliveries_by_owner: int = 0


class CompanionInitiativeAuthority:
    """Deterministic in-memory authority used by isolated tests and no-DB development."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, _SessionInitiativeState] = {}

    def register_generation(self, session_id: str, generation: str) -> InitiativeSnapshot:
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionInitiativeState())
            if state.generation != generation:
                state.generation = generation
                state.active = None
            return _snapshot(session_id, state)

    def acquire(self, request: InitiativeAcquireRequest) -> InitiativeDecision:
        with self._lock:
            state = self._sessions.setdefault(request.session_id, _SessionInitiativeState())
            return _acquire_from_state(state, request)

    def authorizes(self, lease: InitiativeLease, *, now: datetime) -> bool:
        """Return true only while this exact lease is the active session authority."""

        with self._lock:
            state = self._sessions.get(lease.session_id)
            if state is None or state.generation != lease.generation:
                return False
            _expire_active(state, now)
            return state.active == lease

    def bind_intent(
        self,
        *,
        session_id: str,
        lease_id: str,
        intent_id: str,
        bound_at: datetime,
    ) -> InitiativeLease | None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return None
            return _bind_intent_state(
                state,
                lease_id=lease_id,
                intent_id=intent_id,
                bound_at=bound_at,
            )

    def finish(
        self,
        *,
        session_id: str,
        lease_id: str,
        finished_at: datetime,
        delivered: bool,
    ) -> bool:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return False
            return _finish_state(
                state,
                lease_id=lease_id,
                finished_at=finished_at,
                delivered=delivered,
            )

    def finish_intent(
        self,
        *,
        session_id: str,
        intent_id: str,
        finished_at: datetime,
        delivered: bool,
    ) -> bool:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return False
            return _finish_intent_state(
                state,
                intent_id=intent_id,
                finished_at=finished_at,
                delivered=delivered,
            )

    def snapshot(self, session_id: str, *, now: datetime | None = None) -> InitiativeSnapshot:
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionInitiativeState())
            _expire_active(state, now or _utcnow())
            return _snapshot(session_id, state)

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


class PostgresCompanionInitiativeAuthority:
    """Cross-worker initiative authority serialized by a PostgreSQL session row lock."""

    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()

    def register_generation(self, session_id: str, generation: str) -> InitiativeSnapshot:
        with self.database.transaction() as connection:
            state = _locked_postgres_state(connection, session_id)
            if state.generation != generation:
                state.generation = generation
                state.active = None
                _persist_postgres_state(connection, session_id, state)
            return _snapshot(session_id, state)

    def acquire(self, request: InitiativeAcquireRequest) -> InitiativeDecision:
        with self.database.transaction() as connection:
            state = _locked_postgres_state(connection, request.session_id)
            decision = _acquire_from_state(state, request)
            if decision.reason != "stale_generation":
                _persist_postgres_state(connection, request.session_id, state)
            return decision

    def authorizes(self, lease: InitiativeLease, *, now: datetime) -> bool:
        with self.database.transaction() as connection:
            state = _locked_postgres_state(connection, lease.session_id)
            if state.generation != lease.generation:
                return False
            expired = _expire_active(state, now)
            if expired:
                _persist_postgres_state(connection, lease.session_id, state)
            return state.active == lease

    def bind_intent(
        self,
        *,
        session_id: str,
        lease_id: str,
        intent_id: str,
        bound_at: datetime,
    ) -> InitiativeLease | None:
        with self.database.transaction() as connection:
            state = _locked_postgres_state(connection, session_id)
            active = _bind_intent_state(
                state,
                lease_id=lease_id,
                intent_id=intent_id,
                bound_at=bound_at,
            )
            _persist_postgres_state(connection, session_id, state)
            return active

    def finish(
        self,
        *,
        session_id: str,
        lease_id: str,
        finished_at: datetime,
        delivered: bool,
    ) -> bool:
        with self.database.transaction() as connection:
            state = _locked_postgres_state(connection, session_id)
            finished = _finish_state(
                state,
                lease_id=lease_id,
                finished_at=finished_at,
                delivered=delivered,
            )
            _persist_postgres_state(connection, session_id, state)
            return finished

    def finish_intent(
        self,
        *,
        session_id: str,
        intent_id: str,
        finished_at: datetime,
        delivered: bool,
    ) -> bool:
        with self.database.transaction() as connection:
            state = _locked_postgres_state(connection, session_id)
            finished = _finish_intent_state(
                state,
                intent_id=intent_id,
                finished_at=finished_at,
                delivered=delivered,
            )
            _persist_postgres_state(connection, session_id, state)
            return finished

    def snapshot(self, session_id: str, *, now: datetime | None = None) -> InitiativeSnapshot:
        with self.database.transaction() as connection:
            state = _locked_postgres_state(connection, session_id)
            expired = _expire_active(state, now or _utcnow())
            if expired:
                _persist_postgres_state(connection, session_id, state)
            return _snapshot(session_id, state)

    def reset(self, session_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM omnix_companion_initiative_sessions WHERE session_id = %s",
                (session_id,),
            )


def _acquire_from_state(
    state: _SessionInitiativeState,
    request: InitiativeAcquireRequest,
) -> InitiativeDecision:
    if state.generation is None:
        state.generation = request.generation
    if state.generation != request.generation:
        return InitiativeDecision(accepted=False, reason="stale_generation")

    _expire_active(state, request.requested_at)
    if state.active is not None:
        if _can_preempt(state.active, request):
            lease = _new_lease(request)
            state.active = lease
            return InitiativeDecision(
                accepted=True,
                reason="preempted_by_critical_interrupt",
                lease=lease,
                eligible_in_ms=0,
            )
        remaining = max(
            0,
            int((state.active.expires_at - request.requested_at).total_seconds() * 1000),
        )
        return InitiativeDecision(
            accepted=False,
            reason="initiative_active",
            eligible_in_ms=remaining,
        )

    spacing = _effective_spacing_seconds(state, request)
    if state.last_delivered_at is not None and spacing > 0:
        elapsed = (request.requested_at - state.last_delivered_at).total_seconds()
        if elapsed < spacing:
            return InitiativeDecision(
                accepted=False,
                reason="initiative_spacing",
                eligible_in_ms=max(0, int((spacing - elapsed) * 1000)),
            )

    lease = _new_lease(request)
    state.active = lease
    return InitiativeDecision(
        accepted=True,
        reason="initiative_acquired",
        lease=lease,
    )


def _bind_intent_state(
    state: _SessionInitiativeState,
    *,
    lease_id: str,
    intent_id: str,
    bound_at: datetime,
) -> InitiativeLease | None:
    _expire_active(state, bound_at)
    active = state.active
    if active is None or active.lease_id != lease_id:
        return None
    active = active.model_copy(update={"intent_id": intent_id})
    state.active = active
    return active


def _finish_state(
    state: _SessionInitiativeState,
    *,
    lease_id: str,
    finished_at: datetime,
    delivered: bool,
) -> bool:
    _expire_active(state, finished_at)
    active = state.active
    if active is None or active.lease_id != lease_id:
        return False
    state.active = None
    if delivered:
        if state.last_delivered_owner == active.owner:
            state.consecutive_deliveries_by_owner += 1
        else:
            state.last_delivered_owner = active.owner
            state.consecutive_deliveries_by_owner = 1
        state.last_delivered_at = finished_at
    return True


def _finish_intent_state(
    state: _SessionInitiativeState,
    *,
    intent_id: str,
    finished_at: datetime,
    delivered: bool,
) -> bool:
    _expire_active(state, finished_at)
    active = state.active
    if active is None or active.intent_id != intent_id:
        return False
    return _finish_state(
        state,
        lease_id=active.lease_id,
        finished_at=finished_at,
        delivered=delivered,
    )


def _new_lease(request: InitiativeAcquireRequest) -> InitiativeLease:
    return InitiativeLease(
        lease_id=f"lease:{uuid.uuid4().hex}",
        session_id=request.session_id,
        generation=request.generation,
        owner=request.owner,
        intent_id=request.intent_id,
        channel=request.channel,
        urgency=request.urgency,
        interruptibility=request.interruptibility,
        acquired_at=request.requested_at,
        expires_at=request.requested_at + timedelta(seconds=request.ttl_seconds),
    )


def _expire_active(state: _SessionInitiativeState, now: datetime) -> bool:
    if state.active is not None and state.active.expires_at <= now:
        state.active = None
        return True
    return False


def _can_preempt(active: InitiativeLease, request: InitiativeAcquireRequest) -> bool:
    return (
        request.urgency == "critical"
        and request.interruptibility == "interrupt"
        and (
            _URGENCY_RANK[active.urgency] < _URGENCY_RANK[request.urgency]
            or active.interruptibility != "interrupt"
        )
    )


def _effective_spacing_seconds(
    state: _SessionInitiativeState,
    request: InitiativeAcquireRequest,
) -> float:
    if request.urgency == "critical" and request.interruptibility == "interrupt":
        return 0.0
    spacing = request.minimum_spacing_seconds
    if (
        state.last_delivered_owner == request.owner
        and state.consecutive_deliveries_by_owner >= 2
        and request.urgency in {"low", "normal"}
    ):
        return spacing * 2
    if request.urgency == "high":
        return spacing * 0.5
    return spacing


def _snapshot(session_id: str, state: _SessionInitiativeState) -> InitiativeSnapshot:
    return InitiativeSnapshot(
        session_id=session_id,
        generation=state.generation,
        active_lease=state.active,
        last_delivered_at=state.last_delivered_at,
        last_delivered_owner=state.last_delivered_owner,
        consecutive_deliveries_by_owner=state.consecutive_deliveries_by_owner,
    )


def _locked_postgres_state(connection, session_id: str) -> _SessionInitiativeState:
    connection.execute(
        """
        INSERT INTO omnix_companion_initiative_sessions (session_id)
        VALUES (%s)
        ON CONFLICT (session_id) DO NOTHING
        """,
        (session_id,),
    )
    row = connection.execute(
        """
        SELECT session_id, generation,
               active_lease_id, active_owner, active_intent_id, active_channel,
               active_urgency, active_interruptibility,
               active_acquired_at, active_expires_at,
               last_delivered_at, last_delivered_owner,
               consecutive_deliveries_by_owner
          FROM omnix_companion_initiative_sessions
         WHERE session_id = %s
         FOR UPDATE
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("failed to lock companion initiative session row")
    return _postgres_state_from_row(row)


def _postgres_state_from_row(row) -> _SessionInitiativeState:
    generation = str(row[1]) if row[1] is not None else None
    active = None
    if all(row[index] is not None for index in range(2, 10)) and generation is not None:
        active = InitiativeLease(
            lease_id=str(row[2]),
            session_id=str(row[0]),
            generation=generation,
            owner=str(row[3]),
            intent_id=str(row[4]),
            # Stored values; the model validates them on construction.
            channel=cast(InitiativeChannel, str(row[5])),
            urgency=cast(InitiativeUrgency, str(row[6])),
            interruptibility=cast(InitiativeInterruptibility, str(row[7])),
            acquired_at=row[8],
            expires_at=row[9],
        )
    return _SessionInitiativeState(
        generation=generation,
        active=active,
        last_delivered_at=row[10],
        last_delivered_owner=str(row[11]) if row[11] is not None else None,
        consecutive_deliveries_by_owner=int(row[12] or 0),
    )


def _persist_postgres_state(
    connection,
    session_id: str,
    state: _SessionInitiativeState,
) -> None:
    active = state.active
    connection.execute(
        """
        UPDATE omnix_companion_initiative_sessions
           SET generation = %s,
               active_lease_id = %s,
               active_owner = %s,
               active_intent_id = %s,
               active_channel = %s,
               active_urgency = %s,
               active_interruptibility = %s,
               active_acquired_at = %s,
               active_expires_at = %s,
               last_delivered_at = %s,
               last_delivered_owner = %s,
               consecutive_deliveries_by_owner = %s,
               updated_at = CURRENT_TIMESTAMP
         WHERE session_id = %s
        """,
        (
            state.generation,
            active.lease_id if active else None,
            active.owner if active else None,
            active.intent_id if active else None,
            active.channel if active else None,
            active.urgency if active else None,
            active.interruptibility if active else None,
            active.acquired_at if active else None,
            active.expires_at if active else None,
            state.last_delivered_at,
            state.last_delivered_owner,
            state.consecutive_deliveries_by_owner,
            session_id,
        ),
    )


_default_authority: CompanionInitiativeAuthorityStore | None = None
_default_lock = threading.Lock()


def default_companion_initiative_authority() -> CompanionInitiativeAuthorityStore:
    global _default_authority
    if _default_authority is None:
        with _default_lock:
            if _default_authority is None:
                if (_env_str("OMNIX_DATABASE_URL") or "").strip():
                    _default_authority = PostgresCompanionInitiativeAuthority()
                else:
                    _default_authority = CompanionInitiativeAuthority()
    return _default_authority


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "CompanionInitiativeAuthority",
    "CompanionInitiativeAuthorityStore",
    "InitiativeAcquireRequest",
    "InitiativeChannel",
    "InitiativeDecision",
    "InitiativeInterruptibility",
    "InitiativeLease",
    "InitiativeSnapshot",
    "InitiativeUrgency",
    "PostgresCompanionInitiativeAuthority",
    "default_companion_initiative_authority",
]
