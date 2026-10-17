from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from app.apps.rpg.session.narration_trace import record_narration_trace

_SUPPRESS_PROVIDER_RUNTIME_NARRATION: ContextVar[bool] = ContextVar(
    "RPG_SUPPRESS_PROVIDER_RUNTIME_NARRATION",
    default=False,
)


def suppress_provider_runtime_narration() -> bool:
    value = bool(_SUPPRESS_PROVIDER_RUNTIME_NARRATION.get())
    record_narration_trace("guard_check", suppress_provider_runtime_narration=value)
    return value


@contextmanager
def deferred_runtime_narration_context(enabled: bool = True) -> Iterator[None]:
    record_narration_trace("guard_enter", enabled=bool(enabled))
    token = _SUPPRESS_PROVIDER_RUNTIME_NARRATION.set(bool(enabled))
    try:
        yield
    finally:
        _SUPPRESS_PROVIDER_RUNTIME_NARRATION.reset(token)
        record_narration_trace("guard_exit", enabled=bool(enabled))


# WP-8.6: the runtime narration stage (``build_runtime_narration_payload`` in
# ``player_turn_execution.apply_turn``) follows the authoritative turn with a
# provider. The Phase 8.31 synchronous projection would then make a second,
# discarded narration call: the presentation selector keeps the runtime
# narration, and resolved turns are presented by the narrative engine.
_RUNTIME_NARRATION_FOLLOWS: ContextVar[bool] = ContextVar(
    "RPG_RUNTIME_NARRATION_FOLLOWS",
    default=False,
)


def runtime_narration_follows() -> bool:
    return bool(_RUNTIME_NARRATION_FOLLOWS.get())


@contextmanager
def runtime_narration_follows_context(enabled: bool = True) -> Iterator[None]:
    token = _RUNTIME_NARRATION_FOLLOWS.set(bool(enabled))
    try:
        yield
    finally:
        _RUNTIME_NARRATION_FOLLOWS.reset(token)
