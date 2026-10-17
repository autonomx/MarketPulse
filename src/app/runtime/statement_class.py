"""Statement timeouts by work class (WP-5.10).

Request handlers, durable jobs and maintenance tasks need different limits.
Code marks its class with ``statement_class("job")``; each transaction then
sets ``statement_timeout`` locally when the class's limit differs from the
session default (``OMNIX_DATABASE_STATEMENT_TIMEOUT``).
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.config.env import env_int

CLASS_DEFAULTS_MS = {"request": None, "job": 30_000, "maintenance": 120_000}
_CLASS: ContextVar[str | None] = ContextVar("omnix_statement_class", default=None)


@contextmanager
def statement_class(name: str) -> Iterator[None]:
    if name not in CLASS_DEFAULTS_MS:
        raise ValueError(f"unknown statement class {name!r}")
    token = _CLASS.set(name)
    try:
        yield
    finally:
        _CLASS.reset(token)


def current_statement_class() -> str:
    """The class code marked with ``statement_class``; ``default`` when unmarked (requests)."""
    return _CLASS.get() or "default"


def statement_timeout_ms(session_default_ms: int) -> int | None:
    """The current class's limit, or ``None`` when it is the session default."""
    name = _CLASS.get()
    if name is None:
        return None
    default = CLASS_DEFAULTS_MS[name]
    value = env_int(
        f"OMNIX_DATABASE_STATEMENT_TIMEOUT_{name.upper()}",
        default if default is not None else session_default_ms,
        minimum=100,
        maximum=3_600_000,
    )
    return None if value == session_default_ms else value


def apply_statement_class(connection: Any, session_default_ms: int) -> None:
    """Set the class's limit for the current transaction (one statement, only if needed)."""
    timeout = statement_timeout_ms(session_default_ms)
    if timeout is not None:
        connection.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout),))


__all__ = ["apply_statement_class", "current_statement_class", "statement_class", "statement_timeout_ms"]
