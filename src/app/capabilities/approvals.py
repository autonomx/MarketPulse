"""Who may approve a capability call (WP-4.5).

An approval is a decision by a principal: a user with the approval
permission. Service and system identities (service tokens, worker contexts)
and agent runs (run tokens) never approve, whatever roles they were given.
"""
from __future__ import annotations

from typing import Literal

from app.config.env import env_str
from app.runtime.tenant_context import current_tenant
from app.security.permissions import has_permission

NON_PRINCIPAL_ROLES = frozenset({"service", "system", "agent_run"})
RiskLevel = Literal["low", "medium", "high"]
_RISK_ORDER: dict[str, int] = {"none": -1, "low": 0, "medium": 1, "high": 2}


class ApproverNotAllowed(PermissionError):
    """The current caller may not approve this call."""


def current_approver(permission: str) -> str:
    """The user id approving now, or ``ApproverNotAllowed``."""
    context = current_tenant()
    if context.roles & NON_PRINCIPAL_ROLES:
        raise ApproverNotAllowed("approver_not_a_principal")
    if not has_permission(context.roles, permission):
        raise ApproverNotAllowed(f"permission_denied:{permission}")
    return context.user_id


def require_approver(permission: str) -> str:
    """``current_approver`` for HTTP handlers: refusal is a 403."""
    from fastapi import HTTPException

    try:
        return current_approver(permission)
    except ApproverNotAllowed as exc:
        raise HTTPException(
            status_code=403, detail={"error": "approval_not_allowed", "reason": str(exc)}
        ) from exc


def self_approval_max_risk() -> str:
    """Highest risk a user may approve on their own request.

    ``OMNIX_APPROVAL_SELF_ALLOWED_MAX_RISK``: ``none``, ``low``, ``medium`` or
    ``high``. Without sign-in there is one local user, who approves their own
    requests (default ``high``); with sign-in the default is ``low``.
    """
    configured = (env_str("OMNIX_APPROVAL_SELF_ALLOWED_MAX_RISK", "") or "").strip().lower()
    if configured:
        if configured not in _RISK_ORDER:
            raise ValueError("OMNIX_APPROVAL_SELF_ALLOWED_MAX_RISK must be none, low, medium or high")
        return configured
    from app.security.auth.settings import resolve_auth_settings

    return "low" if resolve_auth_settings().enforced else "high"


def require_self_approval_allowed(*, requested_by: str | None, approver: str, risk_level: str) -> None:
    """Refuse self-approval above the configured risk ceiling."""
    if requested_by is None or requested_by != approver:
        return
    if _RISK_ORDER.get(risk_level, _RISK_ORDER["high"]) > _RISK_ORDER[self_approval_max_risk()]:
        raise ApproverNotAllowed("self_approval_not_allowed")


__all__ = [
    "ApproverNotAllowed",
    "NON_PRINCIPAL_ROLES",
    "current_approver",
    "require_approver",
    "require_self_approval_allowed",
    "self_approval_max_risk",
]
