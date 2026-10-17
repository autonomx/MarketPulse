"""Context-local policy for staging RPG session writes into an outer transaction."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from app.apps.rpg.foundation.safe_values import freeze_state, thaw_state


_DEFER_SESSION_SAVE: ContextVar[bool] = ContextVar(
    "omnix_rpg_defer_session_save",
    default=False,
)


def session_save_deferred() -> bool:
    """Return whether the current turn owns persistence through an outer unit of work."""

    return bool(_DEFER_SESSION_SAVE.get())


# While a turn defers its writes the saved game does not change until the turn
# commits, so the turn's stages, which each load it, share one load (WP-5.6):
# the first load reads, normalizes and replays it; later loads thaw a fresh
# copy of that result without asking the database again. The turn's commit
# drops the entry (``forget_turn_session``). A write by someone else in the
# meantime is still caught: the commit is conditional on the revision.
_TURN_LOADS: ContextVar[dict[str, Any] | None] = ContextVar(
    "omnix_rpg_turn_session_loads",
    default=None,
)


# The stored rng seed each save checks against; it cannot change during a turn.
_TURN_SEEDS: ContextVar[dict[str, tuple[bool, Any]] | None] = ContextVar(
    "omnix_rpg_turn_session_seeds",
    default=None,
)


@contextmanager
def rpg_session_save_policy(*, defer: bool) -> Iterator[None]:
    """Temporarily stage compatibility session saves for an atomic foreground turn."""

    token = _DEFER_SESSION_SAVE.set(bool(defer))
    loads = _TURN_LOADS.set({} if defer else None)
    seeds = _TURN_SEEDS.set({} if defer else None)
    try:
        yield
    finally:
        _TURN_SEEDS.reset(seeds)
        _TURN_LOADS.reset(loads)
        _DEFER_SESSION_SAVE.reset(token)


def turn_persisted_rng_seed(session_id: str, load: Callable[[str], tuple[bool, Any]]) -> tuple[bool, Any]:
    """The saved game's rng seed, read once per turn (``load`` reads it)."""
    seeds = _TURN_SEEDS.get()
    if seeds is None:
        return load(session_id)
    if session_id not in seeds:
        seeds[session_id] = load(session_id)
    return seeds[session_id]


def cached_turn_session(session_id: str) -> dict[str, Any] | None:
    """A fresh copy of the session this turn already loaded, if any."""
    loads = _TURN_LOADS.get()
    frozen = loads.get(session_id) if loads is not None else None
    return thaw_state(frozen) if frozen is not None else None


def remember_turn_session(session_id: str, session: dict[str, Any]) -> None:
    """Keep the session this turn loaded for its later loads."""
    loads = _TURN_LOADS.get()
    if loads is not None:
        loads[session_id] = freeze_state(session)


def forget_turn_session(session_id: str) -> None:
    """The turn committed: its later loads read the committed session."""
    loads = _TURN_LOADS.get()
    if loads is not None:
        loads.pop(session_id, None)
