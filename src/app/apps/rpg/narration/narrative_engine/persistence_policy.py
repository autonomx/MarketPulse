"""Context-local policy for staging canonical responses into a larger transaction."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_DEFER_REPOSITORY_SAVE: ContextVar[bool] = ContextVar(
    "omnix_rpg_defer_narrative_repository_save",
    default=False,
)


def repository_save_deferred() -> bool:
    """Return whether the current turn will persist canon in an outer transaction."""

    return bool(_DEFER_REPOSITORY_SAVE.get())


@contextmanager
def narrative_repository_save_policy(*, defer: bool) -> Iterator[None]:
    """Temporarily stage canonical responses instead of committing them separately."""

    token = _DEFER_REPOSITORY_SAVE.set(bool(defer))
    try:
        yield
    finally:
        _DEFER_REPOSITORY_SAVE.reset(token)
