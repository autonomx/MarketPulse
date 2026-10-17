"""Authentication mode and session policy resolved from configuration (WP-4.1)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import ipaddress
import logging
from typing import Literal, cast

from app.config.env import env_bool, env_int, env_list, env_str

logger = logging.getLogger(__name__)

# Sign-in is on unless OMNIX_AUTH_MODE=disabled (WP-4.1; the owner approved the
# default flip on 2026-10-06). Opening Omnix from the launcher still signs the
# owner in automatically with a single-use link.
AUTH_ENFORCED_WHEN_UNSET = True

RegistrationPolicy = Literal["open", "invite", "closed"]
GOOGLE_ISSUER = "https://accounts.google.com"


class AuthMode(str, Enum):
    LOCAL = "local"
    OIDC = "oidc"
    DISABLED = "disabled"


class AuthConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OidcSettings:
    issuer: str
    client_id: str
    client_secret: str | None
    redirect_uri: str
    scopes: tuple[str, ...]
    api_audience: str | None
    allowed_domains: tuple[str, ...]
    required_group: str | None
    groups_claim: str
    workspace_id: str
    default_role: str


@dataclass(frozen=True, slots=True)
class AccountPolicy:
    """Who may get an account in local mode, and how long sign-ins last."""

    # open: anyone may register; invite: only with an invite link; closed: nobody.
    registration: RegistrationPolicy = "invite"
    guests: bool = False
    # "Stay signed in": idle and absolute lifetime of such a session.
    remember_ttl_seconds: int = 30 * 86400
    # A guest account lives this long; then its session ends for good.
    guest_ttl_seconds: int = 7 * 86400
    # Sign in with Google, when configured (OMNIX_GOOGLE_*).
    google: OidcSettings | None = None
    # MarketPulse S-10: the owner may set a first password from a fresh launcher sign-in code.
    launcher_first_password: bool = False
    # MarketPulse S-5: Google sign-in through the system browser for the desktop app's windows.
    desktop_handoff: bool = False


@dataclass(frozen=True, slots=True)
class AuthSettings:
    mode: AuthMode
    enforced: bool
    explicit: bool
    cookie_secure: bool
    sliding_ttl_seconds: int
    absolute_ttl_seconds: int
    login_code_ttl_seconds: int
    oidc: OidcSettings | None = None
    accounts: AccountPolicy = AccountPolicy()


def _oidc_settings(env: Mapping[str, str] | None) -> OidcSettings:
    issuer = (env_str("OMNIX_OIDC_ISSUER", "", env=env) or "").strip().rstrip("/")
    client_id = (env_str("OMNIX_OIDC_CLIENT_ID", "", env=env) or "").strip()
    redirect_uri = (env_str("OMNIX_OIDC_REDIRECT_URI", "", env=env) or "").strip()
    if not issuer or not client_id or not redirect_uri:
        raise AuthConfigurationError(
            "OMNIX_AUTH_MODE=oidc requires OMNIX_OIDC_ISSUER, OMNIX_OIDC_CLIENT_ID "
            "and OMNIX_OIDC_REDIRECT_URI"
        )
    if not issuer.startswith("https://") and not _loopback_url(issuer):
        raise AuthConfigurationError("OMNIX_OIDC_ISSUER must use https")
    role = (env_str("OMNIX_OIDC_DEFAULT_ROLE", "member", env=env) or "member").strip()
    if role not in {"member", "viewer"}:
        # Just-in-time provisioning must never mint privileged roles.
        raise AuthConfigurationError("OMNIX_OIDC_DEFAULT_ROLE must be member or viewer")
    return OidcSettings(
        issuer=issuer,
        client_id=client_id,
        client_secret=(env_str("OMNIX_OIDC_CLIENT_SECRET", env=env) or None),
        redirect_uri=redirect_uri,
        scopes=env_list("OMNIX_OIDC_SCOPES", ("openid", "email", "profile"), env=env),
        api_audience=(env_str("OMNIX_OIDC_API_AUDIENCE", env=env) or None),
        allowed_domains=tuple(
            domain.strip().lower().lstrip("@")
            for domain in env_list("OMNIX_OIDC_ALLOWED_DOMAINS", (), env=env)
            if domain.strip()
        ),
        required_group=(env_str("OMNIX_OIDC_REQUIRED_GROUP", env=env) or None),
        groups_claim=(env_str("OMNIX_OIDC_GROUPS_CLAIM", "groups", env=env) or "groups"),
        workspace_id=(env_str("OMNIX_OIDC_WORKSPACE_ID", "workspace:local", env=env) or "workspace:local"),
        default_role=role,
    )


def _google_settings(env: Mapping[str, str] | None) -> OidcSettings | None:
    """Sign in with Google: an OIDC client for accounts.google.com, or None."""
    client_id = (env_str("OMNIX_GOOGLE_CLIENT_ID", "", env=env) or "").strip()
    if not client_id:
        return None
    secret = (env_str("OMNIX_GOOGLE_CLIENT_SECRET", "", env=env) or "").strip()
    redirect_uri = (env_str("OMNIX_GOOGLE_REDIRECT_URI", "", env=env) or "").strip()
    if not secret or not redirect_uri:
        raise AuthConfigurationError(
            "OMNIX_GOOGLE_CLIENT_ID requires OMNIX_GOOGLE_CLIENT_SECRET and OMNIX_GOOGLE_REDIRECT_URI "
            "(…/api/auth/google/callback, registered with Google)"
        )
    return OidcSettings(
        issuer=GOOGLE_ISSUER,
        client_id=client_id,
        client_secret=secret,
        redirect_uri=redirect_uri,
        scopes=("openid", "email", "profile"),
        api_audience=None,
        allowed_domains=tuple(
            domain.strip().lower().lstrip("@")
            for domain in env_list("OMNIX_GOOGLE_ALLOWED_DOMAINS", (), env=env)
            if domain.strip()
        ),
        required_group=None,
        groups_claim="groups",
        workspace_id="",
        default_role="member",
    )


def _account_policy(env: Mapping[str, str] | None) -> AccountPolicy:
    deployment = (env_str("OMNIX_ENV", "development", env=env) or "development").strip().lower()
    # A developer's own machine may let anyone it serves register; a deployed
    # instance asks for an invite unless the operator opens it.
    default_registration = "open" if deployment in {"development", "test"} else "invite"
    registration = (env_str("OMNIX_AUTH_REGISTRATION", default_registration, env=env) or default_registration)
    registration = registration.strip().lower()
    if registration not in {"open", "invite", "closed"}:
        raise AuthConfigurationError("OMNIX_AUTH_REGISTRATION must be open, invite or closed")
    guests = env_bool("OMNIX_AUTH_GUESTS", registration == "open", env=env)
    remember_days = env_int("OMNIX_AUTH_REMEMBER_DAYS", 30, minimum=1, maximum=365, env=env)
    guest_days = env_int("OMNIX_AUTH_GUEST_DAYS", 7, minimum=1, maximum=90, env=env)
    return AccountPolicy(
        registration=cast(RegistrationPolicy, registration),
        guests=guests,
        remember_ttl_seconds=remember_days * 86400,
        guest_ttl_seconds=guest_days * 86400,
        google=_google_settings(env),
        launcher_first_password=env_bool("OMNIX_AUTH_LAUNCHER_FIRST_PASSWORD", False, env=env),
        desktop_handoff=env_bool("OMNIX_AUTH_DESKTOP_HANDOFF", False, env=env),
    )


def _loopback_url(url: str) -> bool:
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def resolve_auth_settings(env: Mapping[str, str] | None = None) -> AuthSettings:
    raw = env_str("OMNIX_AUTH_MODE", env=env)
    explicit = raw is not None and bool(raw.strip())
    value = (raw or AuthMode.LOCAL.value).strip().lower()
    try:
        mode = AuthMode(value)
    except ValueError as exc:
        raise AuthConfigurationError(f"unsupported OMNIX_AUTH_MODE {value!r}") from exc
    enforced = mode is not AuthMode.DISABLED and (explicit or AUTH_ENFORCED_WHEN_UNSET)
    sliding_hours = env_int("OMNIX_AUTH_SESSION_IDLE_HOURS", 12, minimum=1, maximum=24 * 30, env=env)
    absolute_days = env_int("OMNIX_AUTH_SESSION_MAX_DAYS", 7, minimum=1, maximum=90, env=env)
    sliding = sliding_hours * 3600
    absolute = max(absolute_days * 86400, sliding)
    return AuthSettings(
        mode=mode,
        enforced=enforced,
        explicit=explicit,
        cookie_secure=env_bool("OMNIX_AUTH_COOKIE_SECURE", False, env=env),
        sliding_ttl_seconds=sliding,
        absolute_ttl_seconds=absolute,
        login_code_ttl_seconds=60,
        oidc=_oidc_settings(env) if enforced and mode is AuthMode.OIDC else None,
        # Accounts, guests and Google belong to local mode; in OIDC mode the
        # organization's identity provider decides who signs in.
        accounts=_account_policy(env) if enforced and mode is AuthMode.LOCAL else AccountPolicy(),
    )


def _loopback_host(host: str) -> bool:
    candidate = host.strip().strip("[]").lower()
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def assert_auth_startup_allowed(
    settings: AuthSettings,
    *,
    bind_host: str | None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Fail startup when authentication is off outside a safe local context."""
    deployment = (env_str("OMNIX_ENV", "development", env=env) or "development").strip().lower()
    if settings.mode is AuthMode.DISABLED:
        if deployment == "test":
            return
        if deployment == "development" and bind_host is not None and _loopback_host(bind_host):
            return
        raise AuthConfigurationError(
            "OMNIX_AUTH_MODE=disabled is allowed only with OMNIX_ENV=test, or "
            "OMNIX_ENV=development bound to a loopback address"
        )
    if not settings.enforced:
        logger.warning("authentication_not_enforced")
