"""Authentication: sessions, local install credential, OIDC (WP-4.1)."""
from __future__ import annotations

import logging

from .middleware import (
    AGENT_RUNTIME_PATTERNS,
    PRINCIPAL_STATE_KEY,
    PUBLIC_PATHS,
    PUBLIC_PREFIXES,
    AuthenticationMiddleware,
    principal_from_scope,
)
from .routes import create_auth_router
from .service import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    AuthenticatedPrincipal,
    Authenticator,
    AuthService,
    IssuedSession,
    csrf_token_for,
)
from .settings import (
    AUTH_ENFORCED_WHEN_UNSET,
    AuthConfigurationError,
    AuthMode,
    AuthSettings,
    OidcSettings,
    assert_auth_startup_allowed,
    resolve_auth_settings,
)

logger = logging.getLogger(__name__)


def bootstrap_authentication(service: AuthService, *, bind_host: str | None) -> None:
    """Startup-only: enforce the mode guard and provision the install credential."""
    assert_auth_startup_allowed(service.settings, bind_host=bind_host)
    logger.info(
        "authentication_mode",
        extra={"mode": service.settings.mode.value, "enforced": service.settings.enforced},
    )
    if service.settings.enforced and service.settings.mode is AuthMode.LOCAL:
        from app.security.service_credentials import (
            install_credential_path,
            load_or_create_protected_token,
        )

        if service.sync_install_credential(load_or_create_protected_token(install_credential_path())):
            logger.warning("install_credential_provisioned_or_rotated")


__all__ = [
    "AGENT_RUNTIME_PATTERNS",
    "AUTH_ENFORCED_WHEN_UNSET",
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "PRINCIPAL_STATE_KEY",
    "PUBLIC_PATHS",
    "PUBLIC_PREFIXES",
    "SESSION_COOKIE",
    "AuthConfigurationError",
    "AuthMode",
    "AuthService",
    "AuthSettings",
    "AuthenticatedPrincipal",
    "AuthenticationMiddleware",
    "Authenticator",
    "IssuedSession",
    "OidcSettings",
    "assert_auth_startup_allowed",
    "bootstrap_authentication",
    "create_auth_router",
    "csrf_token_for",
    "principal_from_scope",
    "resolve_auth_settings",
]
