"""Process and request tenant context primitives for the runtime kernel."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Iterable

LOCAL_USER_ID = "user:local"
LOCAL_WORKSPACE_ID = "workspace:local"
LOCAL_MEMBERSHIP_ID = "membership:local-owner"


class TenantAccessDenied(PermissionError):
    """Raised when a trusted principal cannot access a workspace aggregate."""


@dataclass(frozen=True, slots=True)
class TenantContext:
    user_id: str
    workspace_id: str
    membership_id: str
    roles: frozenset[str]

    def __post_init__(self) -> None:
        for name, value in (
            ("user_id", self.user_id),
            ("workspace_id", self.workspace_id),
            ("membership_id", self.membership_id),
        ):
            if not str(value).strip():
                raise ValueError(f"{name} is required")
        if not self.roles:
            raise ValueError("at least one trusted role is required")

    def has_role(self, *roles: str) -> bool:
        return bool(self.roles.intersection(role for role in roles if role))

    def require_role(self, *roles: str) -> None:
        if not self.has_role(*roles):
            raise TenantAccessDenied(
                f"principal {self.user_id} lacks required role in {self.workspace_id}"
            )

    def require_workspace(self, workspace_id: str) -> None:
        if str(workspace_id) != self.workspace_id:
            raise TenantAccessDenied(
                f"workspace {workspace_id} is outside principal scope {self.workspace_id}"
            )


@dataclass(frozen=True, slots=True)
class TrustedPrincipal:
    user_id: str
    memberships: tuple[TenantContext, ...]

    def for_workspace(self, workspace_id: str) -> TenantContext:
        for context in self.memberships:
            if context.workspace_id == workspace_id:
                return context
        raise TenantAccessDenied(
            f"principal {self.user_id} has no active membership in {workspace_id}"
        )


def tenant_context(
    *,
    user_id: str,
    workspace_id: str,
    membership_id: str,
    roles: Iterable[str],
) -> TenantContext:
    return TenantContext(
        user_id=str(user_id).strip(),
        workspace_id=str(workspace_id).strip(),
        membership_id=str(membership_id).strip(),
        roles=frozenset(str(role).strip() for role in roles if str(role).strip()),
    )


def local_tenant_context() -> TenantContext:
    return TenantContext(
        user_id=LOCAL_USER_ID,
        workspace_id=LOCAL_WORKSPACE_ID,
        membership_id=LOCAL_MEMBERSHIP_ID,
        roles=frozenset({"owner", "admin", "member"}),
    )


_CURRENT_TENANT: ContextVar[TenantContext | None] = ContextVar("omnix_current_tenant", default=None)
_PROCESS_DEFAULT: TenantContext | None = None


def install_process_tenant(context: TenantContext) -> None:
    global _PROCESS_DEFAULT
    if _PROCESS_DEFAULT is not None and _PROCESS_DEFAULT != context:
        raise RuntimeError("process tenant is already installed")
    _PROCESS_DEFAULT = context


def reset_process_tenant_for_tests() -> None:
    global _PROCESS_DEFAULT
    _PROCESS_DEFAULT = None


def current_tenant() -> TenantContext:
    context = _CURRENT_TENANT.get()
    if context is not None:
        return context
    if _PROCESS_DEFAULT is None:
        raise RuntimeError("tenant context is not installed")
    return _PROCESS_DEFAULT


def current_tenant_for(_scope: object) -> TenantContext:
    """Adapt the installed tenant context to legacy database-scoped ports."""
    return current_tenant()


def push_tenant(context: TenantContext) -> Token:
    return _CURRENT_TENANT.set(context)


def pop_tenant(token: Token) -> None:
    _CURRENT_TENANT.reset(token)


class RequestTenant:
    """``self.context`` that follows the current request's tenant (WP-4.2).

    Services built once per process used to capture the tenant at
    construction, so every later request ran as that tenant. With this
    descriptor, an explicitly injected context still wins; otherwise each
    read resolves ``current_tenant()`` for the request (or job) in progress.
    """

    def __set_name__(self, owner: type, name: str) -> None:
        self._slot = f"_{name}_explicit"

    def __get__(self, instance: object, owner: type | None = None) -> Any:
        if instance is None:
            return self
        explicit = instance.__dict__.get(self._slot)
        return explicit if explicit is not None else current_tenant()

    def __set__(self, instance: object, value: TenantContext | None) -> None:
        instance.__dict__[self._slot] = value


@dataclass(frozen=True, slots=True)
class TenantProvider:
    def current(self) -> TenantContext:
        return current_tenant()


__all__ = [
    "LOCAL_MEMBERSHIP_ID",
    "LOCAL_USER_ID",
    "LOCAL_WORKSPACE_ID",
    "TenantAccessDenied",
    "TenantContext",
    "RequestTenant",
    "TenantProvider",
    "TrustedPrincipal",
    "current_tenant",
    "current_tenant_for",
    "install_process_tenant",
    "local_tenant_context",
    "pop_tenant",
    "push_tenant",
    "reset_process_tenant_for_tests",
    "tenant_context",
]
