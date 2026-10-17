"""Audit trail for sensitive actions (WP-4.8).

``record`` writes one row to ``omnix_audit_events`` for the current tenant
and principal. Persistence startup installs the PostgreSQL sink; without one
(unit tests, processes without a database) recording is a no-op.

Details are content-free: prompts, messages and other content become
lengths (``app.observability.content_free_diagnostics``), and secret-looking
keys are dropped. The table is append-only (migration 0107).
"""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.observability.content_free_diagnostics import sanitize_content_free_details

Outcome = Literal["success", "failure", "denied"]

# The audited action vocabulary. Every call site uses one of these.
ACTIONS = frozenset({
    "auth.login",
    "auth.logout",
    "auth.failed",
    "settings.update",
    "secret.set",
    "secret.delete",
    "approval.decide",
    "capability.execute",
    "agent.run.start",
    "agent.run.stop",
    "agent.run.promote",
    "agent.run.unsandboxed",
    "trading.control.update",
    "trading.paper.order",
    "feature.toggle",
    "admin.workspace.update",
    "admin.membership.update",
})

# Writes guarded by these permissions are audited by the permission guards
# after the handler finishes; other actions are recorded where they happen.
PERMISSION_AUDIT_ACTIONS: dict[str, str] = {
    "settings:write": "settings.update",
    "tools:connections:admin": "secret.set",
    "trading:control": "trading.control.update",
    "trading:strategies:admin": "trading.control.update",
    "trading:paper:order": "trading.paper.order",
    "agent:promote": "agent.run.promote",
    "admin:features": "feature.toggle",
    "admin:users": "admin.membership.update",
    "workspace:transfer": "admin.workspace.update",
}

_SECRET_KEY = re.compile(r"(secret|token|password|passphrase|api[_-]?key|authorization|credential|cookie|private[_-]?key)", re.I)
_LOCK = threading.Lock()
_SINK: "AuditSink | None" = None
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    action: str
    target_type: str
    target_id: str
    outcome: Outcome
    workspace_id: str | None
    actor_user_id: str | None
    details: dict[str, Any]


class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None: ...


def install_audit_sink(sink: AuditSink | None) -> None:
    global _SINK
    with _LOCK:
        _SINK = sink


def audit_sink() -> AuditSink | None:
    with _LOCK:
        return _SINK


def _without_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _without_secrets(item) for key, item in value.items() if not _SECRET_KEY.search(str(key))}
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value


def audit_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    """The persisted form of ``details``: no content, no secrets."""
    return _without_secrets(sanitize_content_free_details(dict(details or {})))


def record(
    action: str,
    *,
    target_type: str,
    target_id: str,
    outcome: Outcome = "success",
    details: Mapping[str, Any] | None = None,
    actor_user_id: str | None = None,
    workspace_id: str | None = None,
) -> None:
    """Record ``action`` for the current principal and workspace.

    ``actor_user_id`` and ``workspace_id`` override the context, for events
    before a tenant is bound (sign-in). A failing sink is logged, never
    raised: the audited action has already happened or been refused.
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown audit action {action!r}")
    sink = audit_sink()
    if sink is None:
        return
    from app.runtime.tenant_context import current_tenant

    try:
        context = current_tenant()
    except RuntimeError:
        context = None
    payload = audit_details(details)
    from app.security.request_tenant import current_principal

    principal = current_principal()
    if principal is not None:
        payload.setdefault("auth_method", principal.auth_method)
    event = AuditEvent(
        action=action,
        target_type=str(target_type),
        target_id=str(target_id),
        outcome=outcome,
        workspace_id=workspace_id or (context.workspace_id if context else None),
        actor_user_id=actor_user_id or (context.user_id if context else None),
        details=payload,
    )
    try:
        sink.write(event)
    except Exception:
        logger.exception("audit_write_failed action=%s target_type=%s", action, target_type)


__all__ = [
    "ACTIONS",
    "PERMISSION_AUDIT_ACTIONS",
    "AuditEvent",
    "AuditSink",
    "audit_details",
    "audit_sink",
    "install_audit_sink",
    "record",
]
