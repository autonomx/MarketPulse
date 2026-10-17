"""In-memory authenticator for gateway tests that exercise enforced auth."""
from __future__ import annotations

import secrets

from app.runtime.tenant_context import TenantContext
from app.security.auth import (
    AuthenticatedPrincipal,
    AuthMode,
    AuthSettings,
    csrf_token_for,
    resolve_auth_settings,
)


def enforced_local_settings() -> AuthSettings:
    return resolve_auth_settings({"OMNIX_AUTH_MODE": "local"})


class FakeAuthenticator:
    def __init__(self, settings: AuthSettings | None = None) -> None:
        self.settings = settings or enforced_local_settings()
        self.sessions: dict[str, AuthenticatedPrincipal] = {}
        self.bearers: dict[str, AuthenticatedPrincipal] = {}
        self.fail = False

    @staticmethod
    def _context(user_id: str, roles: tuple[str, ...]) -> TenantContext:
        return TenantContext(
            user_id=user_id,
            workspace_id="workspace:local",
            membership_id=f"membership:{user_id}",
            roles=frozenset(roles),
        )

    def issue_session(self, user_id: str = "user:local", roles: tuple[str, ...] = ("owner",)) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        session_id = secrets.token_hex(32)
        csrf = csrf_token_for(csrf_secret=secrets.token_hex(32), session_id=session_id)
        self.sessions[token] = AuthenticatedPrincipal(
            user_id=user_id,
            context=self._context(user_id, roles),
            auth_method="session:local",
            session_id=session_id,
            csrf_token=csrf,
        )
        return token, csrf

    def issue_bearer(self, user_id: str = "user:api", roles: tuple[str, ...] = ("member",)) -> str:
        token = secrets.token_urlsafe(32)
        self.bearers[token] = AuthenticatedPrincipal(
            user_id=user_id, context=self._context(user_id, roles), auth_method="bearer"
        )
        return token

    def authenticate_session(self, token: str) -> AuthenticatedPrincipal | None:
        if self.fail:
            raise RuntimeError("database unavailable")
        return self.sessions.get(token)

    def authenticate_bearer(self, token: str) -> AuthenticatedPrincipal | None:
        if self.fail:
            raise RuntimeError("database unavailable")
        if self.settings.mode is not AuthMode.OIDC and not self.bearers:
            return None
        return self.bearers.get(token)

    def account_view(self, principal: AuthenticatedPrincipal) -> None:
        # The fake keeps no accounts; the session payload then carries none.
        return None
