"""Role-based authorization with a permission catalog (WP-4.3).

Permissions are ``<domain>:<action>`` strings. Every route is checked:

* a route may declare its permission with ``openapi_extra=requires("x:y")``,
  which replaces the default and appears in the OpenAPI document;
* otherwise the default comes from the owning feature (or the kernel path
  table): its read permission for GET/HEAD, its write permission for any
  other method and for WebSockets.

Roles come from the request's TenantContext (WP-4.2). With sign-in off the
local tenant holds ``owner``, so local installs keep full access.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable, Mapping
import functools
import json
import logging
from types import MappingProxyType
from typing import Any, Literal

import anyio
from fastapi import HTTPException, WebSocketException
from starlette.requests import HTTPConnection

from app.config.env import env_str
from app.observability.metrics import record_auth_rejection
from app.runtime.tenant_context import current_tenant, local_tenant_context

logger = logging.getLogger(__name__)

PERMISSION_EXTRA_KEY = "x-omnix-permission"
ALL = "*"

# Appendix C of the roadmap, plus the read/run permissions the starter
# catalog implies for every feature.
_KERNEL_CATALOG: dict[str, str] = {
    "chat:read": "Chat sessions and messages (read)",
    "chat:write": "Chat sessions and messages (write)",
    "characters:read": "Character profiles (read)",
    "characters:write": "Character profiles (write)",
    "memory:read": "Memory items and settings (read)",
    "memory:write": "Memory items (write)",
    "memory:admin": "Memory management",
    "assets:read": "Assets (read)",
    "assets:write": "Assets (write)",
    "assets:delete": "Assets (delete)",
    "jobs:read": "Job views and event streams",
    "jobs:submit": "Submit jobs",
    "jobs:cancel": "Cancel jobs",
    "tools:read": "Assistant tools (read)",
    "tools:propose": "Propose assistant tool calls",
    "tools:approve": "Approve assistant tool calls",
    "tools:execute": "Execute approved assistant tool calls",
    "tools:connections:admin": "OAuth connections and tool credentials",
    "tools:policy:admin": "Assistant tool enablement and approval policy",
    "agent:read": "Agent runs (read)",
    "agent:run": "Start agent runs",
    "agent:steer": "Steer agent runs",
    "agent:approve": "Approve agent capabilities",
    "agent:promote": "Promote agent results",
    "agent:workflows:admin": "Register and manage workflows",
    "assistant:read": "Assistant (read)",
    "assistant:write": "Assistant (write)",
    "companion:read": "Desktop companion (read)",
    "companion:write": "Desktop companion (write)",
    "research:read": "Research (read)",
    "research:run": "Run research",
    "rpg:read": "RPG (read)",
    "rpg:play": "Play RPG sessions",
    "rpg:author": "Author RPG worlds",
    "rpg:admin": "RPG administration",
    "story:read": "Storyteller (read)",
    "story:write": "Storyteller (write)",
    "trading:read": "Trading data (read)",
    "trading:paper:order": "Place paper orders",
    "trading:control": "Trading controls",
    "trading:strategies:admin": "Trading strategy administration",
    "audiobook:read": "Audiobooks (read)",
    "audiobook:write": "Audiobooks (write)",
    "voice:read": "Voice studio (read)",
    "voice:write": "Voice studio (write)",
    "voice:clone": "Voice cloning (consent governed)",
    "image:read": "Image workspace (read)",
    "image:generate": "Generate images",
    "image:models:admin": "Image model management",
    "settings:read": "Settings (read)",
    "settings:write": "Settings (write)",
    "admin:diagnostics": "Diagnostics",
    "admin:metrics": "Metrics",
    "admin:docs": "API documentation",
    "admin:features": "Feature toggles",
    "admin:users": "User administration",
    "internal:service": "Internal service-to-service routes",
    "workspace:transfer": "Transfer workspace ownership",
    "secrets:export": "Export credentials",
    "client:report": "Report browser errors",
}

def _declared_module_permissions() -> tuple[dict[str, str], dict[str, tuple[str, str]], frozenset[str]]:
    """Permissions modules declare in their declarations.py (PA-4.2): catalog entries,
    feature defaults and the ones the member role holds. A name already defined is refused."""
    from app.persistence.declarations import module_permissions

    catalog: dict[str, str] = {}
    defaults: dict[str, tuple[str, str]] = {}
    member: set[str] = set()
    for declared in module_permissions():
        for permission in (declared.read, declared.write):
            if permission.name in _KERNEL_CATALOG or catalog.get(permission.name, permission.description) != permission.description:
                raise RuntimeError(f"module permission {permission.name!r} is already defined")
            catalog[permission.name] = permission.description
            if permission.member:
                member.add(permission.name)
        defaults[declared.feature_id.replace("-", "_")] = (declared.read.name, declared.write.name)
    return catalog, defaults, frozenset(member)


_module_catalog, _module_defaults, _MODULE_MEMBER = _declared_module_permissions()
# Every permission: the kernel catalog above and the ones modules declare (PA-4.2).
CATALOG: Mapping[str, str] = MappingProxyType({**_KERNEL_CATALOG, **_module_catalog})

_ADMIN_EXCLUDED = frozenset({"workspace:transfer", "secrets:export", "internal:service"})
_MEMBER = frozenset({
    "chat:read", "chat:write", "characters:read", "characters:write",
    "memory:read", "memory:write", "assets:read", "assets:write", "assets:delete",
    "jobs:read", "jobs:submit", "jobs:cancel", "tools:read", "tools:propose", "tools:execute",
    "agent:read", "agent:run", "agent:steer", "assistant:read", "assistant:write",
    "companion:read", "companion:write", "research:read", "research:run",
    "rpg:read", "rpg:play", "rpg:author", "story:read", "story:write",
    "trading:read", "trading:paper:order", "audiobook:read", "audiobook:write",
    "voice:read", "voice:write", "voice:clone", "image:read", "image:generate",
    "settings:read", "client:report",
})
_GUEST_EXCLUDED = frozenset({
    "tools:propose", "tools:execute", "agent:run", "agent:steer", "research:run",
    "trading:paper:order", "voice:clone",
})
DEFAULT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset(CATALOG) - _ADMIN_EXCLUDED,
    "member": _MEMBER | _MODULE_MEMBER,
    "approver": frozenset(name for name in CATALOG if name.endswith(":approve")),
    "viewer": frozenset(name for name in CATALOG if name.endswith(":read") and name != "settings:read") | {"client:report"},
    "service": frozenset({"internal:service"}),
    # Background system contexts never serve requests.
    "system": frozenset(),
    # An agent process proven by its run token (WP-4.6): it may act on its
    # own run through the broker and model gateway, never approve.
    "agent_run": frozenset({"agent:read", "agent:run"}),
    # A guest account (WP-4.1): what a member does in their own workspace,
    # minus tools and agents that act outside the conversation, trading
    # orders, voice cloning and paid research. Signing up lifts the limits.
    "guest": (_MEMBER | _MODULE_MEMBER) - _GUEST_EXCLUDED,
}

# Feature id -> (read permission, write permission).
_KERNEL_FEATURE_DEFAULTS: dict[str, tuple[str, str]] = {
    "agent_runtime": ("agent:read", "agent:run"),
    "assistant_memory": ("memory:read", "memory:write"),
    "assistant_tools": ("tools:read", "tools:propose"),
    "audiobook": ("audiobook:read", "audiobook:write"),
    "character_interactions": ("characters:read", "characters:write"),
    "characters": ("characters:read", "characters:write"),
    "chat": ("chat:read", "chat:write"),
    "companion_activity": ("companion:read", "companion:write"),
    "desktop_companion": ("companion:read", "companion:write"),
    "hermes": ("assistant:read", "assistant:write"),
    "image": ("image:read", "image:generate"),
    "live_speech": ("voice:read", "voice:write"),
    "live_voice": ("voice:read", "voice:write"),
    "research": ("research:read", "research:run"),
    "rpg": ("rpg:read", "rpg:play"),
    "story": ("story:read", "story:write"),
    # Strict by default: member-level routes (paper orders) declare their own.
    "trading": ("trading:read", "trading:control"),
    "voice": ("voice:read", "voice:write"),
}
if set(_module_defaults) & set(_KERNEL_FEATURE_DEFAULTS):
    raise RuntimeError(f"features declare permissions but already have central defaults: {sorted(set(_module_defaults) & set(_KERNEL_FEATURE_DEFAULTS))}")
FEATURE_DEFAULTS: Mapping[str, tuple[str, str]] = MappingProxyType({**_KERNEL_FEATURE_DEFAULTS, **_module_defaults})
del _module_catalog, _module_defaults

# Kernel routes by path prefix (longest match wins). None means public.
KERNEL_DEFAULTS: tuple[tuple[str, tuple[str, str] | None], ...] = (
    ("/health", None),
    ("/ready", None),
    ("/api/health", None),
    ("/api/auth/", None),
    ("/internal/", ("internal:service", "internal:service")),
    ("/events", ("jobs:read", "jobs:read")),
    ("/api/jobs", ("jobs:read", "jobs:submit")),
    ("/api/chat", ("chat:read", "chat:write")),
    ("/api/sessions", ("chat:read", "chat:write")),
    ("/api/assets", ("assets:read", "assets:write")),
    ("/api/reports", ("assets:read", "assets:write")),
    ("/api/replay", ("rpg:read", "rpg:play")),
    ("/api/tts", ("voice:read", "voice:write")),
    ("/api/settings", ("settings:read", "settings:write")),
    ("/api/client-errors", ("client:report", "client:report")),
    ("/api/assistant/tools", ("tools:read", "tools:propose")),
    ("/api/prompts", ("assistant:read", "assistant:write")),
    ("/api/voice-cloning", ("voice:read", "voice:clone")),
    ("/api/providers", ("settings:read", "settings:write")),
    ("/api/models", ("settings:read", "settings:write")),
    ("/api/model-residency", ("settings:read", "settings:write")),
    ("/api/workers", ("admin:diagnostics", "admin:diagnostics")),
    ("/api/runtime", ("admin:diagnostics", "admin:diagnostics")),
    ("/api/diagnostics", ("admin:diagnostics", "admin:diagnostics")),
    ("/metrics", ("admin:metrics", "admin:metrics")),
    ("/api/compatibility", ("admin:diagnostics", "admin:diagnostics")),
    ("/openapi.json", ("admin:docs", "admin:docs")),
    ("/docs", ("admin:docs", "admin:docs")),
    ("/redoc", ("admin:docs", "admin:docs")),
)

# Per-route permissions that differ from the owning feature's default,
# reviewed in one place: (method, route path) -> permission.
ROUTE_PERMISSIONS: dict[tuple[str, str], str] = {
    # Research's assistant routes moved from chat (PA-1.3) and keep chat's permissions.
    ("GET", "/api/assistant/research/status"): "chat:read",
    ("PATCH", "/api/assistant/context/research/jobs/{job_id}/plan"): "chat:write",
    ("POST", "/api/assistant/context/research/jobs/{job_id}/start"): "chat:write",
    # Approvals need an approver or admin, never just a member.
    ("POST", "/api/assistant/tools/proposals/{proposal_id}/approve"): "tools:approve",
    ("POST", "/api/hermes/approve"): "tools:approve",
    ("POST", "/api/hermes/rpg/approved-flow"): "tools:approve",
    # Credentials and connections.
    ("POST", "/api/assistant/tools/connect/{tool_id}/oauth-client"): "tools:connections:admin",
    # Enabling tools and loosening their approval policy is the workspace
    # admin's decision; a member may only propose calls.
    ("POST", "/api/assistant/tools/config"): "tools:policy:admin",
    ("POST", "/api/assistant/research/credentials"): "tools:connections:admin",
    ("PUT", "/api/trading/execution/providers/alpaca-iex/credentials"): "trading:control",
    ("PUT", "/api/trading/market-data/providers/coinmarketcap/credentials"): "trading:control",
    # Agent workflows are administered; running them is a member action.
    ("POST", "/api/workflows"): "agent:workflows:admin",
    ("POST", "/api/chat/sessions/{session_id}/live/material/promote"): "agent:promote",
    # Memory and runtime administration.
    ("POST", "/api/assistant/memory/reset"): "memory:admin",
    ("POST", "/api/image-generation/service/start"): "image:models:admin",
    ("POST", "/api/image-generation/model/ensure-loaded"): "image:models:admin",
    ("POST", "/api/image-generation/model/download"): "image:models:admin",
    ("POST", "/api/image-generation/model/load"): "image:models:admin",
    ("POST", "/api/image-generation/model/unload"): "image:models:admin",
    ("POST", "/api/tts/runtime/unload"): "settings:write",
    # Choosing the TTS engine changes Omnix's default and swaps the loaded model.
    ("POST", "/api/tts/engines/select"): "settings:write",
    ("POST", "/api/jobs/{job_id}/cancel"): "jobs:cancel",
    ("POST", "/api/image-generation/assets/{asset_id}/delete"): "assets:delete",
    # Trading: members place paper orders; strategy changes are admin.
    ("POST", "/api/trading/paper/accounts/{account_id}/orders"): "trading:paper:order",
    ("POST", "/api/trading/paper/accounts/{account_id}/risk-orders"): "trading:paper:order",
    ("DELETE", "/api/trading/paper/accounts/{account_id}/orders/{order_id}"): "trading:paper:order",
    ("POST", "/api/trading/paper/accounts/{account_id}/orders/{order_id}/replace"): "trading:paper:order",
    ("POST", "/api/trading/paper/accounts/{account_id}/risk-orders/{order_id}/move"): "trading:paper:order",
    ("POST", "/api/trading/replay/execution/orders"): "trading:paper:order",
    ("POST", "/api/trading/strategies"): "trading:strategies:admin",
    ("PUT", "/api/trading/strategies/{strategy_id}"): "trading:strategies:admin",
    ("DELETE", "/api/trading/strategies/{strategy_id}"): "trading:strategies:admin",
}

# WebSockets cannot declare ``openapi_extra``; read-only feeds are listed here
# and every other socket needs its feature's write permission.
WEBSOCKET_PERMISSIONS: dict[str, str] = {
    "/api/trading/stream": "trading:read",
    "/ws/audiobook": "audiobook:read",
}

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def requires(permission: str) -> dict[str, Any]:
    """``openapi_extra`` for a route that needs a specific permission."""
    if permission not in CATALOG:
        raise ValueError(f"unknown permission {permission!r}")
    return {PERMISSION_EXTRA_KEY: permission}


def role_permissions() -> dict[str, frozenset[str]]:
    """Role mapping, overridable with ``OMNIX_ROLE_PERMISSIONS`` (JSON)."""
    mapping = dict(DEFAULT_ROLE_PERMISSIONS)
    raw = (env_str("OMNIX_ROLE_PERMISSIONS", "") or "").strip()
    if raw:
        overrides = json.loads(raw)
        for role, permissions in overrides.items():
            unknown = [name for name in permissions if name not in CATALOG]
            if unknown or role == "owner":
                raise ValueError(f"invalid OMNIX_ROLE_PERMISSIONS entry for {role!r}: {unknown}")
            mapping[str(role)] = frozenset(permissions)
    return mapping


def has_permission(roles: Iterable[str], permission: str) -> bool:
    granted = set(roles)
    if "owner" in granted:
        return True
    mapping = role_permissions()
    return any(permission in mapping.get(role, frozenset()) for role in granted)


def declared_permission(route: Any, method: str | None = None) -> str | None:
    """A route's own permission: ``openapi_extra`` or the central table."""
    extra = getattr(route, "openapi_extra", None) or {}
    value = extra.get(PERMISSION_EXTRA_KEY) if isinstance(extra, dict) else None
    if value:
        return str(value)
    if method is not None:
        return ROUTE_PERMISSIONS.get((method.upper(), str(getattr(route, "path", ""))))
    return None


def _is_read(connection: HTTPConnection) -> bool:
    return connection.scope["type"] == "http" and connection.scope.get("method", "GET").upper() in _READ_METHODS


def kernel_defaults_for(path: str) -> tuple[str, str] | None | Literal[False]:
    """Kernel (read, write) pair for a path; None = public; False = unmapped."""
    best: tuple[int, tuple[str, str] | None] | None = None
    for prefix, pair in KERNEL_DEFAULTS:
        base = prefix.rstrip("/")
        if path == base or path.startswith(base + "/"):
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), pair)
    if best is None:
        return False
    return best[1]


def _deny(connection: HTTPConnection, permission: str) -> None:
    record_auth_rejection("permission_denied")
    # Every failed access-control decision is logged (ASVS 7.2.2); the log
    # context adds the request id and the hashed user.
    logger.warning(
        "permission_denied permission=%s method=%s path=%s",
        permission, connection.scope.get("method", "WEBSOCKET"), connection.scope.get("path", ""),
    )
    if connection.scope["type"] == "websocket":
        raise WebSocketException(code=1008, reason="permission_denied")
    raise HTTPException(status_code=403, detail={"error": "permission_denied", "permission": permission})


def _caller_roles() -> frozenset[str]:
    try:
        return current_tenant().roles
    except RuntimeError:
        # A signed-in caller always carries its tenant. Without one, sign-in
        # is off and the gateway runs without persistence (benchmarks, tests):
        # the caller is the local owner, as with a bootstrapped install.
        from app.security.request_tenant import current_principal

        if current_principal() is not None:
            raise
        return local_tenant_context().roles


def _enforce(connection: HTTPConnection, permission: str | None) -> None:
    if permission is None:
        return
    if not has_permission(_caller_roles(), permission):
        _deny(connection, permission)


async def _authorized_and_audited(connection: HTTPConnection, permission: str | None) -> AsyncIterator[None]:
    """Enforce ``permission``; audit writes that need an audited one (WP-4.8)."""
    from app.security.audit import PERMISSION_AUDIT_ACTIONS

    action = None
    if permission is not None and connection.scope["type"] == "http" and not _is_read(connection):
        action = PERMISSION_AUDIT_ACTIONS.get(permission)
    if action is None:
        _enforce(connection, permission)
        yield
        return
    route = connection.scope.get("route")
    target = str(getattr(route, "path", None) or connection.scope.get("path") or "")
    details = {"method": str(connection.scope.get("method", "")), "permission": permission}
    try:
        _enforce(connection, permission)
    except HTTPException:
        await _record(action, target, "denied", {**details, "status": 403})
        raise
    try:
        yield
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        await _record(action, target, "denied" if status in {401, 403} else "failure", {**details, "status": status})
        raise
    await _record(action, target, "success", details)


async def _record(
    action: str, target: str, outcome: Literal["success", "failure", "denied"], details: dict
) -> None:
    from app.security.audit import record

    await anyio.to_thread.run_sync(
        functools.partial(record, action, target_type="route", target_id=target, outcome=outcome, details=details)
    )


def feature_permission_guard(feature_id: str) -> Callable[[HTTPConnection], AsyncIterator[None]]:
    normalized = feature_id.replace("-", "_")
    if normalized not in FEATURE_DEFAULTS:
        raise RuntimeError(f"feature {feature_id!r} has no default permissions (app.security.permissions)")
    read, write = FEATURE_DEFAULTS[normalized]

    async def guard(connection: HTTPConnection) -> AsyncIterator[None]:
        route = connection.scope.get("route")
        declared = declared_permission(route, connection.scope.get("method"))
        if declared is None and connection.scope["type"] == "websocket":
            declared = WEBSOCKET_PERMISSIONS.get(str(getattr(route, "path", "")))
        permission = declared or (read if _is_read(connection) else write)
        async for _ in _authorized_and_audited(connection, permission):
            yield

    guard.__name__ = "permission_guard_" + normalized
    return guard


def ensure_permission(permission: str) -> None:
    """For handlers whose required permission depends on the payload."""
    if permission not in CATALOG:
        raise ValueError(f"unknown permission {permission!r}")
    if not has_permission(_caller_roles(), permission):
        record_auth_rejection("permission_denied")
        logger.warning("permission_denied permission=%s", permission)
        raise HTTPException(status_code=403, detail={"error": "permission_denied", "permission": permission})


def internal_permission_guard(connection: HTTPConnection) -> None:
    """Feature internal routers: service-to-service calls only."""
    _enforce(connection, "internal:service")


def _kernel_permission(connection: HTTPConnection) -> str | None:
    declared = declared_permission(connection.scope.get("route"), connection.scope.get("method"))
    if declared is not None:
        return declared
    route = connection.scope.get("route")
    path = str(getattr(route, "path", None) or connection.scope.get("path") or "")
    pair = kernel_defaults_for(path)
    if pair is False:
        # Fail closed: a kernel route nobody classified is not reachable.
        logger.error("kernel route without a permission mapping: %s", path)
        _deny(connection, "unmapped")
    if not pair:
        return None
    return pair[0] if _is_read(connection) else pair[1]


async def kernel_permission_guard(connection: HTTPConnection) -> AsyncIterator[None]:
    permission = _kernel_permission(connection)
    async for _ in _authorized_and_audited(connection, permission):
        yield


__all__ = [
    "ALL",
    "CATALOG",
    "DEFAULT_ROLE_PERMISSIONS",
    "FEATURE_DEFAULTS",
    "KERNEL_DEFAULTS",
    "PERMISSION_EXTRA_KEY",
    "ROUTE_PERMISSIONS",
    "WEBSOCKET_PERMISSIONS",
    "declared_permission",
    "ensure_permission",
    "feature_permission_guard",
    "has_permission",
    "internal_permission_guard",
    "kernel_defaults_for",
    "kernel_permission_guard",
    "requires",
    "role_permissions",
]
