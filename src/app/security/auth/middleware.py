"""Deny-by-default authentication for HTTP and WebSocket requests (WP-4.1).

Credentials accepted, in order:

* the ``omnix_session`` cookie (browsers; unsafe methods also need CSRF);
* ``Authorization: Bearer`` access tokens (OIDC mode, API clients);
* the internal service token, on internal routes only;
* ``Authorization: OmnixRun`` run tokens, on agent-runtime routes only
  (WP-4.6), whether or not sign-in is enforced.

Tokens in query parameters are never read.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
import hmac
import logging
import re

import anyio
from starlette.responses import JSONResponse
from starlette.routing import compile_path
from starlette.types import ASGIApp, Receive, Scope, Send

from app.observability.metrics import record_auth_rejection
from app.runtime.tenant_context import LOCAL_USER_ID, LOCAL_WORKSPACE_ID, TenantContext
from app.security.run_tokens import AGENT_RUNTIME_PATTERNS, SANDBOX_RELAY_HEADER, RunTokenClaims
from app.security.service_token import valid_service_token

from .service import (
    CSRF_HEADER,
    SECURE_SESSION_COOKIE,
    SESSION_COOKIE,
    AuthenticatedPrincipal,
    Authenticator,
)

logger = logging.getLogger(__name__)

PRINCIPAL_STATE_KEY = "omnix_principal"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
PUBLIC_PATHS = frozenset({"/health", "/ready", "/api/health"})
PUBLIC_PREFIXES = ("/api/auth/",)
INTERNAL_PREFIXES = ("/internal/",)

# Routes called by the Pi broker, guard and model-provider extensions. They
# accept only a run token for the run they name (WP-4.6), never a session or
# a bare run id header; the list is pinned by a test so it cannot grow.
_AGENT_RUNTIME_PATH = re.compile("^(?:" + "|".join(AGENT_RUNTIME_PATTERNS) + ")$")
_AGENT_RUN_PATH = re.compile(r"^/api/agent-runs/([^/]+)/")
AGENT_RUN_ROLE = "agent_run"
RUN_TOKEN_STATE_KEY = "omnix_run_token"


def _header_values(scope: Scope, name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == name]


def _cookie(scope: Scope, name: str) -> str | None:
    # Parse pairs directly: SimpleCookie silently keeps only the last
    # duplicate within one header.
    found: list[str] = []
    for raw in _header_values(scope, b"cookie"):
        for pair in raw.split(";"):
            key, separator, value = pair.strip().partition("=")
            if separator and key.strip() == name:
                found.append(value.strip().strip('"'))
    # Duplicate session cookies (for example a planted parent-domain cookie)
    # are ambiguous; refuse rather than guess.
    return found[0] if len(found) == 1 else None


def _templates(paths: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(compile_path(path)[0] for path in paths)


def run_token_claims(scope: Scope) -> RunTokenClaims | None:
    """Claims of the run token that authenticated this request, if any."""
    state = scope.get("state") or {}
    return state.get(RUN_TOKEN_STATE_KEY) if isinstance(state, dict) else None


def _agent_run_principal(scope: Scope) -> AuthenticatedPrincipal | None:
    """The run named by the path (or model-gateway header), proven by its token."""
    from urllib.parse import unquote

    from app.security.run_tokens import RunTokenError, token_from_authorization, verify_run_token

    path = str(scope.get("path") or "")
    match = _AGENT_RUN_PATH.match(path)
    if match:
        run_id = unquote(match.group(1))
    else:
        headers = _header_values(scope, b"x-omnix-agent-run-id")
        run_id = headers[0] if len(headers) == 1 else ""
    token = token_from_authorization(_header_values(scope, b"authorization"))
    if not run_id or token is None:
        return None
    try:
        claims = verify_run_token(token, run_id=run_id)
    except RunTokenError:
        return None
    user_id = f"agent-run:{claims.run_id}"
    context = TenantContext(
        user_id=user_id,
        workspace_id=claims.workspace_id,
        membership_id=f"run:{claims.run_id}",
        roles=frozenset({AGENT_RUN_ROLE}),
    )
    scope.setdefault("state", {})[RUN_TOKEN_STATE_KEY] = claims
    return AuthenticatedPrincipal(user_id=user_id, context=context, auth_method="run_token")


def _local_service_principal() -> AuthenticatedPrincipal:
    context = TenantContext(
        user_id=LOCAL_USER_ID,
        workspace_id=LOCAL_WORKSPACE_ID,
        membership_id="service",
        roles=frozenset({"service"}),
    )
    return AuthenticatedPrincipal(user_id=LOCAL_USER_ID, context=context, auth_method="service")


class AuthenticationMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        authenticator_factory: Callable[[], Authenticator | None],
    ) -> None:
        self.app = app
        self._authenticator_factory = authenticator_factory
        self._authenticator: Authenticator | None = None
        self._resolved = False
        self._route_cache: tuple[int, tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]] | None = None

    def _authenticator_or_none(self) -> Authenticator | None:
        if not self._resolved:
            self._authenticator = self._authenticator_factory()
            self._resolved = True
        return self._authenticator

    def _route_sets(self, scope: Scope) -> tuple[tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]]:
        state = getattr(scope.get("app"), "state", None)
        public = tuple(getattr(state, "public_route_paths", ()) or ())
        internal = tuple(getattr(state, "internal_route_paths", ()) or ())
        key = hash((public, internal))
        if self._route_cache is None or self._route_cache[0] != key:
            self._route_cache = (key, _templates(public), _templates(internal))
        return self._route_cache[1], self._route_cache[2]

    def _classify(self, scope: Scope, path: str) -> str:
        public, internal = self._route_sets(scope)
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES) or any(p.match(path) for p in public):
            return "public"
        if path.startswith(INTERNAL_PREFIXES) or any(p.match(path) for p in internal):
            return "internal"
        return "protected"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "/")
        if _AGENT_RUNTIME_PATH.match(path):
            # Agent processes authenticate with their run token whether or
            # not sign-in is enforced (WP-4.6).
            run_principal = _agent_run_principal(scope)
            if run_principal is None:
                await self._reject(scope, receive, send, 401, "run_token_required")
                return
            scope.setdefault("state", {})[PRINCIPAL_STATE_KEY] = run_principal
            await self.app(scope, receive, send)
            return
        if _header_values(scope, SANDBOX_RELAY_HEADER.encode("ascii")):
            # A sandboxed agent reaches only its broker and model gateway,
            # whether or not sign-in is enforced (WP-4.7). The relay refuses
            # other paths first; this holds if it ever does not.
            await self._reject(scope, receive, send, 403, "sandbox_route_refused")
            return
        authenticator = self._authenticator_or_none()
        if authenticator is None or not authenticator.settings.enforced:
            await self.app(scope, receive, send)
            return

        kind = self._classify(scope, path)
        if kind == "public":
            await self.app(scope, receive, send)
            return
        if kind == "internal":
            if valid_service_token(_header_values(scope, b"x-omnix-service-token")):
                scope.setdefault("state", {})[PRINCIPAL_STATE_KEY] = _local_service_principal()
                await self.app(scope, receive, send)
                return

        try:
            principal, via_cookie = await self._authenticate(scope, authenticator)
        except Exception:
            logger.exception("authentication_backend_failed")
            await self._reject(scope, receive, send, 503, "authentication_unavailable")
            return
        if principal is None:
            await self._reject(scope, receive, send, 401, "authentication_required")
            return
        method = str(scope.get("method", "")).upper()
        if via_cookie and scope["type"] == "http" and method in _UNSAFE_METHODS:
            supplied = _header_values(scope, CSRF_HEADER.encode("ascii"))
            expected = principal.csrf_token or ""
            if len(supplied) != 1 or not expected or not hmac.compare_digest(supplied[0], expected):
                await self._reject(scope, receive, send, 403, "csrf_failed")
                return
        scope.setdefault("state", {})[PRINCIPAL_STATE_KEY] = principal
        await self.app(scope, receive, send)

    async def _authenticate(
        self, scope: Scope, authenticator: Authenticator
    ) -> tuple[AuthenticatedPrincipal | None, bool]:
        authorization = _header_values(scope, b"authorization")
        if authorization:
            if len(authorization) != 1:
                return None, False
            scheme, _, token = authorization[0].partition(" ")
            if scheme.lower() != "bearer" or not token.strip():
                return None, False
            principal = await anyio.to_thread.run_sync(authenticator.authenticate_bearer, token.strip())
            return principal, False
        session = _cookie(scope, SECURE_SESSION_COOKIE) or _cookie(scope, SESSION_COOKIE)
        if session:
            principal = await anyio.to_thread.run_sync(authenticator.authenticate_session, session)
            return principal, True
        return None, False

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
        record_auth_rejection(detail)
        # Authentication and CSRF refusals are logged (ASVS 7.2.1); the
        # presented credential never is.
        logger.info("request_rejected reason=%s status=%s path=%s", detail, status, scope.get("path", ""))
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": detail})
            return
        headers = {"WWW-Authenticate": 'Bearer realm="omnix"'} if status == 401 else None
        await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, send)


def principal_from_scope(scope: Scope) -> AuthenticatedPrincipal | None:
    state = scope.get("state") or {}
    principal = state.get(PRINCIPAL_STATE_KEY) if isinstance(state, dict) else None
    return principal if isinstance(principal, AuthenticatedPrincipal) else None
