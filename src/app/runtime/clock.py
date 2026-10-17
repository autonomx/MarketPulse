"""Clock contracts and turn-scoped time capture for runtime code."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Protocol


class Clock(Protocol):
    """Wall and monotonic time required by runtime-owned behavior."""

    def now(self) -> datetime:
        """Return the current UTC instant."""

    def monotonic(self) -> float:
        """Return a monotonic timestamp for elapsed-time measurements."""


class SystemClock:
    """Production clock implementation."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.perf_counter()


SYSTEM_CLOCK = SystemClock()


@dataclass(frozen=True)
class TurnContext:
    """Clock dependency and the one wall-clock instant captured at a boundary."""

    clock: Clock
    now: datetime
    session_seed: int | None = None
    turn_index: int | None = None
    session_id: str | None = None

    @classmethod
    def capture(
        cls,
        clock: Clock = SYSTEM_CLOCK,
        *,
        session_seed: int | None = None,
        turn_index: int | None = None,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> "TurnContext":
        now = now if now is not None else clock.now()
        if now.tzinfo is None:
            raise ValueError("Clock.now() must return a timezone-aware datetime")
        return cls(
            clock=clock,
            now=now.astimezone(timezone.utc),
            session_seed=session_seed,
            turn_index=turn_index,
            session_id=session_id,
        )


_TURN_CONTEXT: ContextVar[TurnContext | None] = ContextVar(
    "omnix_runtime_turn_context", default=None
)


@contextmanager
def bind_turn_context(context: TurnContext) -> Iterator[None]:
    """Make a captured turn input available to nested runtime helpers."""

    token: Token[TurnContext | None] = _TURN_CONTEXT.set(context)
    try:
        yield
    finally:
        _TURN_CONTEXT.reset(token)


def current_turn_context() -> TurnContext | None:
    return _TURN_CONTEXT.get()


def utc_now(clock: Clock | None = None) -> datetime:
    """Return the turn's recorded time, or read the supplied/system clock."""

    context = current_turn_context()
    now = context.now if context is not None else (clock or SYSTEM_CLOCK).now()
    if now.tzinfo is None:
        raise ValueError("Clock.now() must return a timezone-aware datetime")
    return now.astimezone(timezone.utc)
