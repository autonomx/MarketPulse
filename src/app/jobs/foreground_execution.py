"""Carry the original foreground claim within one trusted execution scope."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator


@dataclass(frozen=True)
class ForegroundExecution:
    workspace_id: str
    session_id: str
    submission_id: str
    job_id: str
    claim_token: str = field(repr=False)


_EXECUTION: ContextVar[ForegroundExecution | None] = ContextVar(
    "omnix_foreground_execution", default=None
)


def current_foreground_execution() -> ForegroundExecution | None:
    return _EXECUTION.get()


@contextmanager
def foreground_execution(execution: ForegroundExecution) -> Iterator[None]:
    if not all((execution.workspace_id, execution.session_id, execution.submission_id,
                execution.job_id, execution.claim_token)):
        raise ValueError("foreground execution requires the original claim")
    token = _EXECUTION.set(execution)
    try:
        yield
    finally:
        _EXECUTION.reset(token)
