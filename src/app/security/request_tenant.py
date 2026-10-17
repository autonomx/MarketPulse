"""Per-request principal and tenant context (WP-4.2).

Runs after authentication. Every request (HTTP and WebSocket) gets the
caller's TenantContext pushed for its whole lifetime, so repositories that
resolve ``current_tenant()`` act for that caller and workspace:

* an authenticated principal's default workspace, or the workspace named by
  ``X-Omnix-Workspace`` when the principal is an active member of it;
* without a principal (sign-in not enforced), the process's local tenant;
  ``X-Omnix-Workspace`` may still select another workspace of the local user.

A workspace the caller does not belong to is refused with 403; it is never
silently replaced by a default.
"""
from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
import logging

import anyio
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.runtime.tenant_context import (
    LOCAL_USER_ID,
    TenantAccessDenied,
    TenantContext,
    current_tenant,
    pop_tenant,
    push_tenant,
)
from app.security.auth.middleware import principal_from_scope
from app.security.auth.service import AuthenticatedPrincipal

logger = logging.getLogger(__name__)

WORKSPACE_HEADER = b"x-omnix-workspace"
MembershipResolver = Callable[[str, str], TenantContext]

_CURRENT_PRINCIPAL: ContextVar[AuthenticatedPrincipal | None] = ContextVar(
    "omnix_current_principal", default=None
)


def current_principal() -> AuthenticatedPrincipal | None:
    """The authenticated caller of the current request, if any."""
    return _CURRENT_PRINCIPAL.get()


def load_membership(user_id: str, workspace_id: str) -> TenantContext:
    """Active membership of ``user_id`` in ``workspace_id`` from PostgreSQL."""
    from app.persistence.database import default_database
    from app.persistence.tenant_scope import system_scope
    from app.persistence.unit_of_work import unit_of_work

    # The caller's workspace is not bound yet (WP-4.4 row-level security).
    with system_scope("identity.memberships"), unit_of_work(default_database()) as work:
        try:
            return work.identities.load_context(user_id=user_id, workspace_id=workspace_id)
        finally:
            work.rollback()


class RequestTenantMiddleware:
    def __init__(self, app: ASGIApp, *, resolver: MembershipResolver | None = None) -> None:
        self.app = app
        self._resolver = resolver or load_membership

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        requested = [
            value.decode("latin-1").strip()
            for key, value in scope.get("headers", [])
            if key.lower() == WORKSPACE_HEADER
        ]
        if len(requested) > 1 or (requested and not requested[0]):
            await _reject(scope, receive, send, 400, "workspace_header_invalid")
            return
        principal = principal_from_scope(scope)
        try:
            context = await self._context_for(principal, requested[0] if requested else None)
        except TenantAccessDenied:
            await _reject(scope, receive, send, 403, "workspace_access_denied")
            return
        if context is None:
            await self.app(scope, receive, send)
            return
        tenant_token = push_tenant(context)
        principal_token = _CURRENT_PRINCIPAL.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            _CURRENT_PRINCIPAL.reset(principal_token)
            pop_tenant(tenant_token)

    async def _context_for(
        self, principal: AuthenticatedPrincipal | None, workspace_id: str | None
    ) -> TenantContext | None:
        if principal is None:
            if workspace_id is None:
                return None  # keep the process tenant (local install)
            base = current_tenant()
            if workspace_id == base.workspace_id:
                return base
            return await anyio.to_thread.run_sync(self._resolver, LOCAL_USER_ID, workspace_id)
        if workspace_id is None or workspace_id == principal.context.workspace_id:
            return principal.context
        return await anyio.to_thread.run_sync(self._resolver, principal.user_id, workspace_id)


async def _reject(scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008, "reason": detail})
        return
    await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)


def require_principal() -> AuthenticatedPrincipal | None:
    """FastAPI dependency: the request's principal (None when sign-in is off)."""
    return current_principal()


__all__ = [
    "MembershipResolver",
    "RequestTenantMiddleware",
    "WORKSPACE_HEADER",
    "current_principal",
    "load_membership",
    "require_principal",
]
