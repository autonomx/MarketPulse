"""Session, account, install-credential and login-code authentication (WP-4.1).

Local mode (the default) offers, as its account policy allows: email and
password accounts, guest accounts, invite links, Google sign-in, "stay signed
in" sessions, single-use launcher links and the owner's install credential.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import logging
import re
import secrets
import uuid
from typing import TYPE_CHECKING, Any, Literal, Protocol

from app.runtime.tenant_context import (
    LOCAL_USER_ID,
    LOCAL_WORKSPACE_ID,
    TenantAccessDenied,
    TenantContext,
)

from .repository import InviteSummary, PostgresAuthRepository, SessionSummary
from .settings import GOOGLE_ISSUER, AuthMode, AuthSettings

if TYPE_CHECKING:
    from .oidc import OidcClient

logger = logging.getLogger(__name__)

SESSION_COOKIE = "omnix_session"
# The session cookie's name over HTTPS: the browser accepts it only with
# Secure, Path=/ and no Domain, so no other host can set or read it (ASVS 3.4.4).
SECURE_SESSION_COOKIE = "__Host-omnix_session"
CSRF_COOKIE = "omnix_csrf"
CSRF_HEADER = "x-omnix-csrf"

# A desktop Google sign-in (MarketPulse S-5) has as long as a browser one: account choice and two-step checks.
DESKTOP_HANDOFF_TTL_SECONDS = 600

_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_token_for(*, csrf_secret: str, session_id: str) -> str:
    return hmac.new(csrf_secret.encode("ascii"), session_id.encode("ascii"), hashlib.sha256).hexdigest()


def _scrypt(credential: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        credential.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_SCRYPT_MAXMEM, dklen=32
    )


# Signing other devices out needs a fresh proof of identity (ASVS 3.3.4): the
# install credential in local mode, a sign-in this recent in OIDC mode.
REAUTHENTICATION_WINDOW_SECONDS = 10 * 60


MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 512
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$")
# An unknown email still costs one scrypt run, so timing does not reveal accounts.
_DUMMY_SALT = b"omnix-no-such-account"
INVITE_TTL_SECONDS = 7 * 86400
GUEST_ROLES = ("guest",)
OWNER_ROLES = ("owner", "admin", "member")


class AccountError(ValueError):
    """A sign-up, sign-in or account change was refused; ``code`` is safe to show."""

    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def normalize_email(value: str) -> str:
    email = (value or "").strip().lower()
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        raise AccountError("invalid_email")
    return email


def check_password(password: str, *, email: str | None = None) -> None:
    """ASVS 2.1: at least 12 characters, at most 512, any characters, not the email."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError("password_too_short")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AccountError("password_too_long")
    if email and password.strip().lower() == email:
        raise AccountError("password_is_email")


def _display_name(value: str | None, email: str | None) -> str:
    name = " ".join((value or "").split())[:200]
    if name:
        return name
    return email.split("@", 1)[0][:200] if email else "Omnix user"


@dataclass(frozen=True, slots=True)
class AccountView:
    """The signed-in account, as the web app shows it."""

    user_id: str
    display_name: str
    email: str | None
    kind: str
    has_password: bool
    google_linked: bool
    is_owner: bool


@dataclass(frozen=True, slots=True)
class IssuedInvite:
    token: str
    expires_at: Any


class ReauthenticationRequired(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """The authenticated caller. WP-4.2 builds request tenancy on top of this."""

    user_id: str
    context: TenantContext
    auth_method: str
    session_id: str | None = None
    csrf_token: str | None = None


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    csrf_token: str
    principal: AuthenticatedPrincipal
    max_age_seconds: int


@dataclass(frozen=True, slots=True)
class DesktopHandoffOutcome:
    """What a desktop window's nonce brings back (MarketPulse S-5)."""

    status: Literal["pending", "signed_in", "linked", "failed"]
    redirect_after: str
    session: IssuedSession | None = None
    error: str | None = None


class Authenticator(Protocol):
    """What the request middleware needs; tests substitute an in-memory fake."""

    settings: AuthSettings

    def authenticate_session(self, token: str) -> AuthenticatedPrincipal | None: ...

    def authenticate_bearer(self, token: str) -> AuthenticatedPrincipal | None: ...


class AuthService:
    def __init__(
        self,
        settings: AuthSettings,
        *,
        database: Any | None = None,
        oidc: Any | None = None,
        unit_of_work_factory: Callable[..., Any] | None = None,
        google: Any | None = None,
    ) -> None:
        self.settings = settings
        self._database = database
        self._oidc = oidc
        self._google: Any | None = google
        self._unit_of_work_factory = unit_of_work_factory

    # Infrastructure -------------------------------------------------------

    @contextmanager
    def _work(self) -> Iterator[Any]:
        # Sessions and login codes are looked up before the caller's
        # workspace is known, so they bypass row-level security (WP-4.4).
        from app.persistence.tenant_scope import system_scope

        with system_scope("auth.sessions"), self._unit_of_work() as work:
            yield work

    def _unit_of_work(self) -> Any:
        if self._unit_of_work_factory is not None:
            return self._unit_of_work_factory()
        from app.persistence.database import default_database
        from app.persistence.unit_of_work import unit_of_work

        if self._database is None:
            self._database = default_database()
        return unit_of_work(self._database)

    @property
    def oidc(self) -> OidcClient:
        if self._oidc is None:
            if self.settings.oidc is None:
                raise RuntimeError("oidc_not_configured")
            from .oidc import OidcClient

            self._oidc = OidcClient(self.settings.oidc)
        return self._oidc

    # Sessions -------------------------------------------------------------

    def _issue(
        self,
        work: Any,
        context: TenantContext,
        *,
        auth_method: str,
        user_agent: str | None,
        remember: bool = False,
    ) -> IssuedSession:
        token = secrets.token_urlsafe(32)
        session_id = digest(token)
        csrf_secret = secrets.token_hex(32)
        policy = self.settings.accounts
        own_sliding: int | None = None
        absolute = self.settings.absolute_ttl_seconds
        if auth_method == "guest":
            # A guest has no way back in: the session is the account's lifetime.
            own_sliding = absolute = policy.guest_ttl_seconds
        elif remember:
            own_sliding = absolute = max(policy.remember_ttl_seconds, self.settings.sliding_ttl_seconds)
        PostgresAuthRepository(work.connection).create_session(
            session_id=session_id,
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            auth_method=auth_method,
            csrf_secret=csrf_secret,
            user_agent_hash=digest(user_agent) if user_agent else None,
            sliding_seconds=self.settings.sliding_ttl_seconds,
            absolute_seconds=absolute,
            own_sliding_seconds=own_sliding,
        )
        work.audit.append(
            context,
            aggregate_type="auth_session",
            aggregate_id=session_id[:16],
            action="auth.login",
            payload={"auth_method": auth_method},
        )
        csrf = csrf_token_for(csrf_secret=csrf_secret, session_id=session_id)
        return IssuedSession(
            token=token,
            csrf_token=csrf,
            principal=AuthenticatedPrincipal(
                user_id=context.user_id,
                context=context,
                auth_method=f"session:{auth_method}",
                session_id=session_id,
                csrf_token=csrf,
            ),
            max_age_seconds=absolute,
        )

    def authenticate_session(self, token: str) -> AuthenticatedPrincipal | None:
        if not token or len(token) > 256:
            return None
        session_id = digest(token)
        with self._work() as work:
            record = PostgresAuthRepository(work.connection).active_session(
                session_id, sliding_seconds=self.settings.sliding_ttl_seconds
            )
            if record is None:
                work.rollback()
                return None
            try:
                context = work.identities.load_context(
                    user_id=record.user_id, workspace_id=record.workspace_id
                )
            except TenantAccessDenied:
                # A disabled user or removed membership ends every session.
                work.rollback()
                return None
            work.commit()
        return AuthenticatedPrincipal(
            user_id=context.user_id,
            context=context,
            auth_method=f"session:{record.auth_method}",
            session_id=session_id,
            csrf_token=csrf_token_for(csrf_secret=record.csrf_secret, session_id=session_id),
        )

    def authenticate_bearer(self, token: str) -> AuthenticatedPrincipal | None:
        if self.settings.mode is not AuthMode.OIDC or self.settings.oidc is None:
            return None
        if self.settings.oidc.api_audience is None:
            return None
        claims = self.oidc.validate_access_token(token)
        if claims is None:
            return None
        with self._work() as work:
            user_id = PostgresAuthRepository(work.connection).external_identity_user(
                issuer=self.settings.oidc.issuer, subject=str(claims["sub"])
            )
            if user_id is None:
                work.rollback()
                return None
            try:
                context = work.identities.load_context(
                    user_id=user_id, workspace_id=self.settings.oidc.workspace_id
                )
            except TenantAccessDenied:
                work.rollback()
                return None
            work.rollback()
        return AuthenticatedPrincipal(user_id=user_id, context=context, auth_method="bearer")

    def logout(self, token: str) -> None:
        session_id = digest(token)
        with self._work() as work:
            repository = PostgresAuthRepository(work.connection)
            record = repository.active_session(session_id, sliding_seconds=self.settings.sliding_ttl_seconds)
            if repository.revoke_session(session_id) and record is not None:
                try:
                    context = work.identities.load_context(
                        user_id=record.user_id, workspace_id=record.workspace_id
                    )
                except TenantAccessDenied:
                    context = None
                if context is not None:
                    work.audit.append(
                        context,
                        aggregate_type="auth_session",
                        aggregate_id=session_id[:16],
                        action="auth.logout",
                        payload={},
                    )
            work.commit()

    def list_sessions(self, principal: AuthenticatedPrincipal) -> list[tuple[SessionSummary, bool]]:
        """The caller's active sessions, each with whether it is this one."""
        with self._work() as work:
            sessions = PostgresAuthRepository(work.connection).user_sessions(principal.user_id)
            work.rollback()
        return [(summary, session_id == principal.session_id) for session_id, summary in sessions]

    def revoke_sessions(
        self, principal: AuthenticatedPrincipal, *, handle: str | None, credential: str | None
    ) -> int:
        """Sign out one of the caller's sessions (by handle) or all the others.

        The caller proves its identity again first; the current session is
        never revoked here (that is logout).
        """
        if principal.session_id is None:
            raise ReauthenticationRequired("a browser session is required")
        with self._work() as work:
            repository = PostgresAuthRepository(work.connection)
            if self.settings.mode is AuthMode.LOCAL:
                if not self._reauthenticated(repository, principal, credential):
                    self._audit_failure(work, "session_revocation")
                    work.commit()
                    raise ReauthenticationRequired("invalid_credential")
            else:
                age = repository.session_age_seconds(principal.session_id)
                if age is None or age > REAUTHENTICATION_WINDOW_SECONDS:
                    work.rollback()
                    raise ReauthenticationRequired("recent_sign_in_required")
            if handle is not None and principal.session_id.startswith(handle):
                work.rollback()
                return 0
            revoked = repository.revoke_user_sessions_matching(
                principal.user_id, handle=handle, keep=principal.session_id
            )
            work.audit.append(
                principal.context,
                aggregate_type="auth_session",
                aggregate_id=principal.session_id[:16],
                action="auth.sessions.revoked",
                payload={"scope": "one" if handle is not None else "others", "revoked": revoked},
            )
            work.commit()
        return revoked

    def _reauthenticated(
        self,
        repository: PostgresAuthRepository,
        principal: AuthenticatedPrincipal,
        credential: str | None,
        *,
        first_password: bool = False,
    ) -> bool:
        """The caller proved it is still them.

        An account with a secret presents it again: its password, or the
        install credential for the owner. An account without one (Google only)
        must have signed in within the last few minutes.

        With ``launcher_first_password`` (MarketPulse S-10), the owner setting a
        *first* password may instead come from a launcher sign-in code (a stored
        ``local`` session) issued within the same few minutes: the desktop app
        signs its first window in that way and asks for a password at once.
        """
        stored_password = repository.password(principal.user_id)
        install = repository.install_credential()
        owner_install = install if install is not None and install.user_id == principal.user_id else None
        if credential and len(credential) <= MAX_PASSWORD_LENGTH:
            if stored_password is not None and hmac.compare_digest(
                _scrypt(credential, stored_password.salt, n=stored_password.n, r=stored_password.r,
                        p=stored_password.p),
                stored_password.credential_hash,
            ):
                return True
            if owner_install is not None and hmac.compare_digest(
                _scrypt(credential, owner_install.salt, n=owner_install.n, r=owner_install.r, p=owner_install.p),
                owner_install.credential_hash,
            ):
                return True
            return False
        if (
            first_password
            and stored_password is None
            and owner_install is not None
            and self.settings.accounts.launcher_first_password
            and principal.session_id
        ):
            session = repository.session_method_and_age(principal.session_id)
            if session is not None and session[0] == "local" and session[1] <= REAUTHENTICATION_WINDOW_SECONDS:
                return True
        if stored_password is not None or owner_install is not None:
            return False
        age = repository.session_age_seconds(principal.session_id) if principal.session_id else None
        return age is not None and age <= REAUTHENTICATION_WINDOW_SECONDS

    # Accounts (local mode) ------------------------------------------------

    def _require_accounts(self) -> None:
        if self.settings.mode is not AuthMode.LOCAL or not self.settings.enforced:
            raise AccountError("accounts_unavailable", 404)

    def _invite_allows(self, repository: PostgresAuthRepository, invite: str | None) -> str | None:
        """The invite's hash when sign-up may proceed, after the registration policy."""
        policy = self.settings.accounts.registration
        invite_hash = digest(invite) if invite else None
        if invite_hash is not None and not repository.invite_usable(invite_hash):
            raise AccountError("invite_invalid", 403)
        if invite_hash is None and policy != "open":
            raise AccountError("registration_closed" if policy == "closed" else "invite_required", 403)
        return invite_hash

    def _new_account(
        self,
        work: Any,
        *,
        display_name: str,
        email: str | None,
        kind: str,
        invite_hash: str | None,
    ) -> TenantContext:
        user_id = f"user:{uuid.uuid4().hex}"
        context = work.identities.create_account_with_workspace(
            user_id=user_id,
            display_name=display_name,
            email=email,
            account_kind=kind,
            roles=GUEST_ROLES if kind == "guest" else OWNER_ROLES,
        )
        if invite_hash is not None and not PostgresAuthRepository(work.connection).consume_invite(
            invite_hash, consumed_by=user_id
        ):
            # Spent by a concurrent sign-up between the check and now.
            raise AccountError("invite_invalid", 403)
        work.audit.append(context, aggregate_type="account", aggregate_id=user_id,
                          action="auth.account.created", payload={"kind": kind, "invited": invite_hash is not None})
        return context

    def _store_password(self, work: Any, user_id: str, password: str) -> None:
        salt = secrets.token_bytes(16)
        PostgresAuthRepository(work.connection).store_password(
            user_id=user_id, salt=salt, credential_hash=_scrypt(password, salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P),
            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        )

    def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None,
        invite: str | None,
        remember: bool,
        user_agent: str | None,
    ) -> IssuedSession:
        """A new account with a workspace of its own, signed in."""
        self._require_accounts()
        address = normalize_email(email)
        check_password(password, email=address)
        with self._work() as work:
            repository = PostgresAuthRepository(work.connection)
            invite_hash = self._invite_allows(repository, invite)
            if work.identities.email_taken(address):
                work.rollback()
                raise AccountError("email_taken", 409)
            context = self._new_account(
                work, display_name=_display_name(display_name, address), email=address, kind="standard",
                invite_hash=invite_hash,
            )
            self._store_password(work, context.user_id, password)
            issued = self._issue(work, context, auth_method="password", user_agent=user_agent, remember=remember)
            work.commit()
        return issued

    def login_with_password(
        self, *, email: str, password: str, remember: bool, user_agent: str | None
    ) -> IssuedSession | None:
        self._require_accounts()
        try:
            address = normalize_email(email)
        except AccountError:
            return None
        if not password or len(password) > MAX_PASSWORD_LENGTH:
            return None
        with self._work() as work:
            repository = PostgresAuthRepository(work.connection)
            user_id = work.identities.user_by_email(address)
            stored = repository.password(user_id) if user_id else None
            if stored is None:
                _scrypt(password, _DUMMY_SALT, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
                self._audit_failure(work, "password")
                work.commit()
                return None
            if not hmac.compare_digest(
                _scrypt(password, stored.salt, n=stored.n, r=stored.r, p=stored.p), stored.credential_hash
            ):
                self._audit_failure(work, "password")
                work.commit()
                return None
            workspace_id = work.identities.primary_workspace(stored.user_id)
            if workspace_id is None:
                work.rollback()
                return None
            context = work.identities.load_context(user_id=stored.user_id, workspace_id=workspace_id)
            issued = self._issue(work, context, auth_method="password", user_agent=user_agent, remember=remember)
            work.commit()
            return issued

    def login_as_guest(self, *, user_agent: str | None) -> IssuedSession:
        """A guest account with its own workspace; it can become a full account later."""
        self._require_accounts()
        if not self.settings.accounts.guests:
            raise AccountError("guests_disabled", 403)
        with self._work() as work:
            context = self._new_account(work, display_name="Guest", email=None, kind="guest", invite_hash=None)
            issued = self._issue(work, context, auth_method="guest", user_agent=user_agent)
            work.commit()
        return issued

    def upgrade_guest(
        self,
        principal: AuthenticatedPrincipal,
        *,
        email: str,
        password: str,
        display_name: str | None,
        remember: bool,
        user_agent: str | None,
    ) -> IssuedSession:
        """Keep everything the guest made: the account becomes a full one."""
        self._require_accounts()
        address = normalize_email(email)
        check_password(password, email=address)
        with self._work() as work:
            identities = work.identities
            account = identities.user_account(principal.user_id)
            if account is None or account["account_kind"] != "guest":
                work.rollback()
                raise AccountError("not_a_guest", 409)
            if identities.email_taken(address, except_user=principal.user_id):
                work.rollback()
                raise AccountError("email_taken", 409)
            identities.update_account(principal.user_id, display_name=_display_name(display_name, address),
                                      email=address, account_kind="standard")
            identities.set_membership_roles(user_id=principal.user_id, workspace_id=principal.context.workspace_id,
                                            roles=OWNER_ROLES)
            self._store_password(work, principal.user_id, password)
            repository = PostgresAuthRepository(work.connection)
            # The guest session (and its short lifetime) ends; a normal one replaces it.
            repository.revoke_user_sessions(principal.user_id)
            context = identities.load_context(user_id=principal.user_id, workspace_id=principal.context.workspace_id)
            work.audit.append(context, aggregate_type="account", aggregate_id=principal.user_id,
                              action="auth.account.upgraded", payload={})
            issued = self._issue(work, context, auth_method="password", user_agent=user_agent, remember=remember)
            work.commit()
        return issued

    def change_password(
        self, principal: AuthenticatedPrincipal, *, current: str | None, new_password: str
    ) -> int:
        """Set or change the caller's password; other sessions are signed out.

        Changing needs the current password. Setting a first one (the owner,
        or a Google-only account) needs the install credential or a recent sign-in.
        """
        self._require_accounts()
        with self._work() as work:
            identities = work.identities
            account = identities.user_account(principal.user_id)
            if account is None or account["account_kind"] == "guest":
                work.rollback()
                raise AccountError("guest_must_sign_up", 409)
            check_password(new_password, email=account["email"])
            repository = PostgresAuthRepository(work.connection)
            if repository.has_password(principal.user_id):
                stored = repository.password(principal.user_id)
                assert stored is not None
                if not current or not hmac.compare_digest(
                    _scrypt(current, stored.salt, n=stored.n, r=stored.r, p=stored.p), stored.credential_hash
                ):
                    self._audit_failure(work, "password_change")
                    work.commit()
                    raise ReauthenticationRequired("invalid_credential")
            elif not self._reauthenticated(repository, principal, current, first_password=True):
                work.rollback()
                raise ReauthenticationRequired("recent_sign_in_required")
            self._store_password(work, principal.user_id, new_password)
            revoked = repository.revoke_user_sessions_matching(principal.user_id, handle=None,
                                                               keep=principal.session_id or "")
            work.audit.append(principal.context, aggregate_type="account", aggregate_id=principal.user_id,
                              action="auth.password.changed", payload={"revoked_sessions": revoked})
            work.commit()
        return revoked

    def set_email(self, principal: AuthenticatedPrincipal, *, email: str, display_name: str | None) -> None:
        """The owner (and Google-only accounts) may add an email to sign in with."""
        self._require_accounts()
        address = normalize_email(email)
        with self._work() as work:
            identities = work.identities
            account = identities.user_account(principal.user_id)
            if account is None or account["account_kind"] == "guest":
                work.rollback()
                raise AccountError("guest_must_sign_up", 409)
            if identities.email_taken(address, except_user=principal.user_id):
                work.rollback()
                raise AccountError("email_taken", 409)
            identities.update_account(principal.user_id,
                                      display_name=_display_name(display_name or account["display_name"], address),
                                      email=address, account_kind=account["account_kind"])
            work.audit.append(principal.context, aggregate_type="account", aggregate_id=principal.user_id,
                              action="auth.account.email_changed", payload={})
            work.commit()

    def account_view(self, principal: AuthenticatedPrincipal) -> AccountView | None:
        with self._work() as work:
            account = work.identities.user_account(principal.user_id)
            repository = PostgresAuthRepository(work.connection)
            has_password = repository.has_password(principal.user_id)
            issuers = repository.linked_issuers(principal.user_id)
            work.rollback()
        if account is None:
            return None
        return AccountView(
            user_id=principal.user_id,
            display_name=account["display_name"],
            email=account["email"],
            kind=account["account_kind"],
            has_password=has_password,
            google_linked=GOOGLE_ISSUER in issuers,
            is_owner=principal.user_id == LOCAL_USER_ID,
        )

    # Invites --------------------------------------------------------------

    def create_invite(self, principal: AuthenticatedPrincipal, *, note: str | None) -> IssuedInvite:
        self._require_accounts()
        token = secrets.token_urlsafe(24)
        with self._work() as work:
            expires_at = PostgresAuthRepository(work.connection).insert_invite(
                token_hash=digest(token), created_by=principal.user_id,
                note=(note or "").strip()[:200] or None, ttl_seconds=INVITE_TTL_SECONDS,
            )
            work.audit.append(principal.context, aggregate_type="auth_invite", aggregate_id=digest(token)[:16],
                              action="auth.invite.created", payload={})
            work.commit()
        return IssuedInvite(token=token, expires_at=expires_at)

    def list_invites(self, principal: AuthenticatedPrincipal) -> list[InviteSummary]:
        with self._work() as work:
            invites = PostgresAuthRepository(work.connection).open_invites(principal.user_id)
            work.rollback()
        return invites

    def revoke_invite(self, principal: AuthenticatedPrincipal, handle: str) -> bool:
        with self._work() as work:
            revoked = PostgresAuthRepository(work.connection).revoke_invite(created_by=principal.user_id, handle=handle)
            work.commit()
        return revoked

    def invite_status(self, invite: str) -> bool:
        if not invite or len(invite) > 256:
            return False
        with self._work() as work:
            usable = PostgresAuthRepository(work.connection).invite_usable(digest(invite))
            work.rollback()
        return usable

    # Google ---------------------------------------------------------------

    @property
    def google(self) -> OidcClient:
        if self._google is None:
            settings = self.settings.accounts.google
            if settings is None:
                raise AccountError("google_not_configured", 404)
            from .oidc import OidcClient

            self._google = OidcClient(settings)
        return self._google

    def start_google_login(
        self,
        *,
        redirect_after: str,
        invite: str | None = None,
        remember: bool = True,
        link_user: str | None = None,
    ) -> tuple[str, str]:
        """(authorization_url, browser_binding) to sign in with, or link, Google."""
        self._require_accounts()
        start = self.google.begin(redirect_after=redirect_after)
        with self._work() as work:
            PostgresAuthRepository(work.connection).insert_oidc_state(
                state_hash=digest(start.state),
                browser_binding_hash=digest(start.browser_binding),
                nonce=start.nonce,
                code_verifier=start.code_verifier,
                redirect_after=start.redirect_after,
                ttl_seconds=600,
                purpose="google_link" if link_user else "google",
                link_user_id=link_user,
                invite_token_hash=digest(invite) if invite else None,
                remember=remember,
            )
            work.commit()
        return start.authorization_url, start.browser_binding

    def complete_google_login(
        self, *, code: str, state: str, browser_binding: str | Sequence[str], user_agent: str | None
    ) -> tuple[IssuedSession | None, str]:
        """Finish a Google sign-in or link. Returns (session or None, where to go).

        ``where to go`` carries an ``error=`` for the sign-in page when it fails.
        Accounts are never merged by email: registration does not verify
        email, so a Google account whose email belongs to another account is
        refused, and its owner links Google from their settings instead.

        A desktop sign-in (MarketPulse S-5) resolves the account the same way
        but signs nobody in here: the identity, or the error, goes on its
        handoff for the app's window, and ``where to go`` is ``desktop:done``
        or ``desktop:error:<code>`` for the system browser's closing page.
        """
        self._require_accounts()
        settings = self.settings.accounts.google
        bindings = [browser_binding] if isinstance(browser_binding, str) else [item for item in browser_binding if item]
        if settings is None or not code or not state or not any(bindings):
            return None, "/login?error=sign_in_failed"
        state_hash = digest(state)
        with self._work() as work:
            login = PostgresAuthRepository(work.connection).consume_oidc_state(
                state_hash=state_hash, browser_binding_hash=[digest(item) for item in bindings if item]
            )
            work.commit()
        if login is None or login.purpose not in {"google", "google_link", "google_desktop", "google_link_desktop"}:
            return None, "/login?error=sign_in_failed"
        desktop = login.purpose.endswith("_desktop")
        purpose = login.purpose.removesuffix("_desktop")

        def failed(target: str) -> tuple[None, str]:
            if not desktop:
                return None, target
            code_name = target.split("=", 1)[1] if "=" in target else "sign_in_failed"
            with self._work() as work:
                PostgresAuthRepository(work.connection).complete_desktop_handoff(state_hash, error=code_name)
                work.commit()
            return None, f"desktop:error:{code_name}"

        claims = self.google.exchange_code(code=code, code_verifier=login.code_verifier, nonce=login.nonce)
        if claims is None:
            return failed("/login?error=sign_in_failed")
        decision = self.google.admission(claims)
        if not decision.allowed:
            return failed("/login?error=google_not_allowed")
        subject = str(claims["sub"])
        verified_email = decision.email if claims.get("email_verified") is True else None
        with self._work() as work:
            repository = PostgresAuthRepository(work.connection)
            identities = work.identities
            linked_user = repository.external_identity_user(issuer=GOOGLE_ISSUER, subject=subject)
            if purpose == "google_link":
                if login.link_user_id is None or (linked_user is not None and linked_user != login.link_user_id):
                    work.rollback()
                    return failed("/settings?account_error=google_linked_elsewhere")
                repository.link_external_identity(issuer=GOOGLE_ISSUER, subject=subject,
                                                  user_id=login.link_user_id, email=verified_email)
                workspace_id = identities.primary_workspace(login.link_user_id)
                if workspace_id is not None:
                    context = identities.load_context(user_id=login.link_user_id, workspace_id=workspace_id)
                    work.audit.append(context, aggregate_type="account", aggregate_id=login.link_user_id,
                                      action="auth.google.linked", payload={})
                if desktop:
                    repository.complete_desktop_handoff(state_hash, user_id=login.link_user_id,
                                                        workspace_id=workspace_id)
                work.commit()
                return None, "desktop:done" if desktop else login.redirect_after
            if linked_user is None:
                try:
                    invite_hash = self._invite_allows(repository, None) if login.invite_token_hash is None else (
                        login.invite_token_hash if repository.invite_usable(login.invite_token_hash) else None
                    )
                except AccountError as exc:
                    work.rollback()
                    return failed(f"/login?error={exc.code}")
                if login.invite_token_hash is not None and invite_hash is None:
                    work.rollback()
                    return failed("/login?error=invite_invalid")
                if verified_email and identities.email_taken(verified_email):
                    work.rollback()
                    return failed("/login?error=google_email_in_use")
                try:
                    context = self._new_account(work, display_name=decision.display_name, email=verified_email,
                                                kind="standard", invite_hash=invite_hash)
                except AccountError as exc:
                    work.rollback()
                    return failed(f"/login?error={exc.code}")
                linked_user = context.user_id
            else:
                workspace_id = identities.primary_workspace(linked_user)
                if workspace_id is None:
                    work.rollback()
                    return failed("/login?error=sign_in_failed")
                context = identities.load_context(user_id=linked_user, workspace_id=workspace_id)
            repository.link_external_identity(issuer=GOOGLE_ISSUER, subject=subject, user_id=linked_user,
                                              email=verified_email)
            if desktop:
                # The system browser gets no session; the app's window takes it with its nonce.
                repository.complete_desktop_handoff(state_hash, user_id=context.user_id,
                                                    workspace_id=context.workspace_id)
                work.commit()
                return None, "desktop:done"
            issued = self._issue(work, context, auth_method="google", user_agent=user_agent, remember=login.remember)
            work.commit()
        return issued, login.redirect_after

    # Desktop Google sign-in (MarketPulse S-5) ------------------------------

    def _require_desktop_handoff(self) -> None:
        self._require_accounts()
        if not self.settings.accounts.desktop_handoff or self.settings.accounts.google is None:
            raise AccountError("desktop_google_unavailable", 404)

    def start_desktop_google(
        self, *, redirect_after: str, invite: str | None = None, remember: bool = True, link_user: str | None = None
    ) -> tuple[str, str]:
        """(nonce, begin_token): the window keeps the nonce; the system browser opens the begin link."""
        self._require_desktop_handoff()
        nonce = secrets.token_urlsafe(32)
        begin_token = secrets.token_urlsafe(32)
        with self._work() as work:
            PostgresAuthRepository(work.connection).insert_desktop_handoff(
                nonce_hash=digest(nonce),
                begin_token_hash=digest(begin_token),
                purpose="google_link" if link_user else "google",
                link_user_id=link_user,
                invite_token_hash=digest(invite) if invite else None,
                remember=remember,
                redirect_after=redirect_after,
                ttl_seconds=DESKTOP_HANDOFF_TTL_SECONDS,
            )
            work.commit()
        return nonce, begin_token

    def begin_desktop_google(self, begin_token: str) -> tuple[str, str] | None:
        """(authorization_url, browser_binding) for the system browser, once per handoff; None when not valid."""
        self._require_desktop_handoff()
        if not begin_token:
            return None
        with self._work() as work:
            repository = PostgresAuthRepository(work.connection)
            handoff = repository.begin_desktop_handoff(digest(begin_token))
            if handoff is None:
                work.rollback()
                return None
            start = self.google.begin(redirect_after=handoff.redirect_after)
            state_hash = digest(start.state)
            repository.insert_oidc_state(
                state_hash=state_hash,
                browser_binding_hash=digest(start.browser_binding),
                nonce=start.nonce,
                code_verifier=start.code_verifier,
                redirect_after=start.redirect_after,
                ttl_seconds=DESKTOP_HANDOFF_TTL_SECONDS,
                purpose=f"{handoff.purpose}_desktop",
                link_user_id=handoff.link_user_id,
                invite_token_hash=handoff.invite_token_hash,
                remember=handoff.remember,
            )
            repository.attach_desktop_state(handoff.nonce_hash, state_hash)
            work.commit()
        return start.authorization_url, start.browser_binding

    def take_desktop_handoff(self, nonce: str, *, user_agent: str | None) -> DesktopHandoffOutcome | None:
        """What the window gets for its nonce: still pending, a session, a linked account, or an error.

        None when the nonce is unknown, already used or expired.
        """
        self._require_desktop_handoff()
        if not nonce:
            return None
        with self._work() as work:
            repository = PostgresAuthRepository(work.connection)
            result = repository.take_desktop_handoff(digest(nonce))
            if result is None:
                work.rollback()
                return None
            if result.pending:
                work.rollback()
                return DesktopHandoffOutcome(status="pending", redirect_after=result.redirect_after)
            if result.error is not None or result.user_id is None:
                work.commit()
                return DesktopHandoffOutcome(status="failed", redirect_after=result.redirect_after,
                                             error=result.error or "sign_in_failed")
            if result.purpose == "google_link":
                work.commit()
                return DesktopHandoffOutcome(status="linked", redirect_after=result.redirect_after)
            workspace_id = result.workspace_id or work.identities.primary_workspace(result.user_id)
            if workspace_id is None:
                work.commit()
                return DesktopHandoffOutcome(status="failed", redirect_after=result.redirect_after,
                                             error="sign_in_failed")
            context = work.identities.load_context(user_id=result.user_id, workspace_id=workspace_id)
            issued = self._issue(work, context, auth_method="google", user_agent=user_agent, remember=result.remember)
            work.commit()
        return DesktopHandoffOutcome(status="signed_in", redirect_after=result.redirect_after, session=issued)

    # Local mode -----------------------------------------------------------

    def sync_install_credential(self, plaintext: str) -> bool:
        """Make the database verifier match the protected install credential.

        Returns True when the verifier changed. A change revokes every local
        session, because whoever held the old credential must sign in again.
        """
        with self._work() as work:
            repository = PostgresAuthRepository(work.connection)
            current = repository.install_credential()
            if current is not None and hmac.compare_digest(
                _scrypt(plaintext, current.salt, n=current.n, r=current.r, p=current.p),
                current.credential_hash,
            ):
                work.rollback()
                return False
            salt = secrets.token_bytes(16)
            repository.store_install_credential(
                user_id=LOCAL_USER_ID,
                workspace_id=LOCAL_WORKSPACE_ID,
                salt=salt,
                credential_hash=_scrypt(plaintext, salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P),
                n=_SCRYPT_N,
                r=_SCRYPT_R,
                p=_SCRYPT_P,
            )
            revoked = repository.revoke_user_sessions(LOCAL_USER_ID) if current is not None else 0
            context = work.identities.load_context(user_id=LOCAL_USER_ID, workspace_id=LOCAL_WORKSPACE_ID)
            work.audit.append(
                context,
                aggregate_type="install_credential",
                aggregate_id="install",
                action="auth.install_credential.provisioned" if current is None else "auth.install_credential.rotated",
                payload={"revoked_sessions": revoked},
            )
            work.commit()
        return True

    def login_with_install_credential(
        self, presented: str, *, user_agent: str | None, remember: bool = False
    ) -> IssuedSession | None:
        if self.settings.mode is not AuthMode.LOCAL or not presented or len(presented) > 512:
            return None
        with self._work() as work:
            stored = PostgresAuthRepository(work.connection).install_credential()
            if stored is None or not hmac.compare_digest(
                _scrypt(presented, stored.salt, n=stored.n, r=stored.r, p=stored.p),
                stored.credential_hash,
            ):
                self._audit_failure(work, "install_credential")
                work.commit()
                return None
            context = work.identities.load_context(user_id=stored.user_id, workspace_id=stored.workspace_id)
            issued = self._issue(work, context, auth_method="local", user_agent=user_agent, remember=remember)
            work.commit()
            return issued

    def issue_login_code(self) -> str:
        """Single-use, 60-second code the launcher exchanges for a session."""
        if self.settings.mode is not AuthMode.LOCAL:
            raise RuntimeError("login codes are available only in local auth mode")
        code = secrets.token_urlsafe(32)
        with self._work() as work:
            PostgresAuthRepository(work.connection).insert_login_code(
                code_hash=digest(code),
                user_id=LOCAL_USER_ID,
                workspace_id=LOCAL_WORKSPACE_ID,
                ttl_seconds=self.settings.login_code_ttl_seconds,
            )
            work.commit()
        return code

    def exchange_login_code(self, code: str, *, user_agent: str | None) -> IssuedSession | None:
        if self.settings.mode is not AuthMode.LOCAL or not code or len(code) > 256:
            return None
        with self._work() as work:
            grant = PostgresAuthRepository(work.connection).consume_login_code(digest(code))
            if grant is None:
                self._audit_failure(work, "login_code")
                work.commit()
                return None
            context = work.identities.load_context(user_id=grant[0], workspace_id=grant[1])
            # The launcher runs on the owner's own machine: stay signed in.
            issued = self._issue(work, context, auth_method="local", user_agent=user_agent, remember=True)
            work.commit()
            return issued

    def _audit_failure(self, work: Any, method: str) -> None:
        # Failed attempts have no authenticated actor; attribute them to the
        # installation workspace without recording the presented secret.
        try:
            context = work.identities.load_context(user_id=LOCAL_USER_ID, workspace_id=LOCAL_WORKSPACE_ID)
        except TenantAccessDenied:
            return
        work.audit.append(
            context,
            aggregate_type="auth",
            aggregate_id=method,
            action="auth.failed",
            payload={"method": method},
        )

    # OIDC mode ------------------------------------------------------------

    def start_oidc_login(self, *, redirect_after: str) -> tuple[str, str]:
        """Return (authorization_url, browser_binding) for a new login."""
        start = self.oidc.begin(redirect_after=redirect_after)
        with self._work() as work:
            PostgresAuthRepository(work.connection).insert_oidc_state(
                state_hash=digest(start.state),
                browser_binding_hash=digest(start.browser_binding),
                nonce=start.nonce,
                code_verifier=start.code_verifier,
                redirect_after=start.redirect_after,
                ttl_seconds=600,
            )
            work.commit()
        return start.authorization_url, start.browser_binding

    def complete_oidc_login(
        self, *, code: str, state: str, browser_binding: str, user_agent: str | None
    ) -> tuple[IssuedSession, str] | None:
        settings = self.settings.oidc
        if settings is None or not code or not state or not browser_binding:
            return None
        with self._work() as work:
            login = PostgresAuthRepository(work.connection).consume_oidc_state(
                state_hash=digest(state), browser_binding_hash=digest(browser_binding)
            )
            work.commit()
        if login is None:
            return None
        claims = self.oidc.exchange_code(code=code, code_verifier=login.code_verifier, nonce=login.nonce)
        if claims is None:
            return None
        decision = self.oidc.admission(claims)
        if not decision.allowed:
            logger.info("oidc_login_rejected", extra={"reason": decision.reason})
            return None
        subject = str(claims["sub"])
        with self._work() as work:
            repository = PostgresAuthRepository(work.connection)
            user_id = repository.external_identity_user(issuer=settings.issuer, subject=subject)
            if user_id is None:
                user_id = f"user:oidc:{digest(settings.issuer + '|' + subject)[:32]}"
            context = work.identities.provision_member(
                user_id=user_id,
                display_name=decision.display_name,
                email=decision.email,
                workspace_id=settings.workspace_id,
                roles=(settings.default_role,),
            )
            repository.link_external_identity(
                issuer=settings.issuer, subject=subject, user_id=user_id, email=decision.email
            )
            issued = self._issue(work, context, auth_method="oidc", user_agent=user_agent)
            work.commit()
        return issued, login.redirect_after
