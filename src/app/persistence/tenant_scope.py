"""Row-level security scope of PostgreSQL sessions (WP-4.4).

Migration 0106 isolates every tenant table with a policy that reads two
session settings:

- ``omnix.workspace_id``: the workspace of the current tenant;
- ``omnix.system``: ``on`` only inside a named system operation that must see
  every workspace (claiming jobs, recovery scans, retention, sign-in lookups).

``apply_session_scope`` sets both when a connection is checked out of the
pool, so a unit of work runs entirely as the tenant that opened it. A query
that forgets its ``workspace_id`` filter still sees only that workspace.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

# Cross-workspace operations, each with the reason it cannot run per tenant.
# Keep this list short and reviewable: everything else runs as a tenant.
SYSTEM_OPERATIONS: dict[str, str] = {
    "identity.provision": "Creates users, workspaces and memberships before the workspace is anyone's tenant.",
    "identity.memberships": "Resolves a signed-in user's memberships across workspaces.",
    "identity.workspaces": "Lists active workspaces so workers can serve each one.",
    "auth.sessions": "Looks up a session or login code before the caller's workspace is known.",
    "migrations": "Data migrations and migration metadata span every workspace.",
    "operator.cli": "Operator commands (cutover, coordinated recovery) act on the whole database.",
    "audit.write": "Audit events record actions before a tenant is bound (sign-in) and for system work.",
    "retention": "Retention deletes rows by age and state across every workspace.",
    "outbox.relay": "The outbox relay claims and delivers committed events of every workspace.",
    "notifications.delivery": "One alert notification monitor claims and records the deliveries of every workspace.",
}

_SYSTEM_OPERATION: ContextVar[str | None] = ContextVar("omnix_system_operation", default=None)
_APPLIED_ATTRIBUTE = "_omnix_session_scope"


@contextmanager
def system_scope(operation: str) -> Iterator[None]:
    """Run the enclosed database work across every workspace.

    Only connections checked out inside the block are affected. Operations
    must be listed in ``SYSTEM_OPERATIONS``.
    """
    if operation not in SYSTEM_OPERATIONS:
        raise ValueError(f"unknown system operation {operation!r}; add it to SYSTEM_OPERATIONS")
    token = _SYSTEM_OPERATION.set(operation)
    try:
        yield
    finally:
        _SYSTEM_OPERATION.reset(token)


def current_system_operation() -> str | None:
    return _SYSTEM_OPERATION.get()


def session_scope() -> tuple[str, str]:
    """Return ``(workspace_id, system)`` for the current execution context."""
    from app.runtime.tenant_context import current_tenant

    try:
        workspace_id = current_tenant().workspace_id
    except RuntimeError:
        # No tenant installed (scripts, migrations): no workspace is visible.
        workspace_id = ""
    return workspace_id, "on" if _SYSTEM_OPERATION.get() else ""


def apply_session_scope(connection: Any) -> None:
    """Set the session's tenant settings unless they already match.

    Runs outside any transaction so that the settings survive rollbacks and
    the caller's first statement still opens its own transaction.
    """
    scope = session_scope()
    if getattr(connection, _APPLIED_ATTRIBUTE, None) == scope:
        return
    autocommit = connection.autocommit
    connection.autocommit = True
    try:
        connection.execute(
            "SELECT set_config('omnix.workspace_id', %s, false), set_config('omnix.system', %s, false)",
            scope,
        )
    finally:
        connection.autocommit = autocommit
    setattr(connection, _APPLIED_ATTRIBUTE, scope)


__all__ = [
    "SYSTEM_OPERATIONS",
    "apply_session_scope",
    "current_system_operation",
    "session_scope",
    "system_scope",
]
