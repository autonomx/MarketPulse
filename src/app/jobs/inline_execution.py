"""Compatibility marker for jobs executed locally without a queue worker."""

from __future__ import annotations

from typing import Any


def mark_inline_execution(request: Any) -> Any:
    """Return a request whose persisted compatibility contract permits local execution."""
    compat = dict(getattr(request, "compat", None) or {})
    compat["inline_execution"] = True
    model_copy = getattr(request, "model_copy", None)
    if callable(model_copy):
        return model_copy(update={"compat": compat})
    setattr(request, "compat", compat)
    return request

def require_execution_authority(job_store: Any, job_id: str) -> None:
    """Fence publication when the caller provides durable execution ownership.

    In-memory compatibility stores intentionally have no checker. Production's
    durable feature worker exposes one that verifies singleton authority plus
    the exact PostgreSQL lease immediately before externally visible writes.
    """
    checker = getattr(job_store, "require_execution_authority", None)
    if callable(checker):
        checker(job_id)

