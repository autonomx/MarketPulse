"""PostgreSQL persistence for authentication state (WP-4.1).

Secrets never reach the database in plaintext: session ids, login codes and
OIDC states are stored as SHA-256 digests, the install credential as scrypt.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Sequence
from typing import Any


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: str
    user_id: str
    workspace_id: str
    auth_method: str
    csrf_secret: str
    expires_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """One active session as its owner sees it: no secrets, a short handle."""

    handle: str
    auth_method: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime


# Sessions are named to their owner by the first 16 hex digits of their id
# (itself a digest of the token): enough to tell them apart, useless to sign in.
SESSION_HANDLE_LENGTH = 16


@dataclass(frozen=True, slots=True)
class InstallCredentialRecord:
    user_id: str
    workspace_id: str
    salt: bytes
    credential_hash: bytes
    n: int
    r: int
    p: int


@dataclass(frozen=True, slots=True)
class DesktopHandoff:
    """A desktop Google sign-in whose begin link the system browser just opened."""

    nonce_hash: str
    purpose: str
    link_user_id: str | None
    invite_token_hash: str | None
    remember: bool
    redirect_after: str


@dataclass(frozen=True, slots=True)
class DesktopHandoffResult:
    pending: bool
    purpose: str
    user_id: str | None
    workspace_id: str | None
    error: str | None
    remember: bool
    redirect_after: str


@dataclass(frozen=True, slots=True)
class OidcLoginState:
    nonce: str
    code_verifier: str
    redirect_after: str
    purpose: str = "oidc"
    link_user_id: str | None = None
    invite_token_hash: str | None = None
    remember: bool = False


@dataclass(frozen=True, slots=True)
class PasswordRecord:
    user_id: str
    salt: bytes
    credential_hash: bytes
    n: int
    r: int
    p: int


@dataclass(frozen=True, slots=True)
class AccountRecord:
    """A user as sign-in sees it."""

    user_id: str
    display_name: str
    email: str | None
    account_kind: str
    status: str
    has_password: bool
    linked_issuers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InviteSummary:
    handle: str
    note: str | None
    created_at: datetime
    expires_at: datetime


_SESSION_COLUMNS = (
    "id, user_id, workspace_id, auth_method, csrf_secret, expires_at, absolute_expires_at"
)
# Sliding expiry is refreshed at most once a minute per session, so ordinary
# request traffic does not rewrite the session row on every call.
_TOUCH_INTERVAL_SECONDS = 60


def _session(row: Any) -> SessionRecord:
    return SessionRecord(
        id=str(row[0]),
        user_id=str(row[1]),
        workspace_id=str(row[2]),
        auth_method=str(row[3]),
        csrf_secret=str(row[4]),
        expires_at=row[5],
        absolute_expires_at=row[6],
    )


class PostgresAuthRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    # Sessions -------------------------------------------------------------

    def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        workspace_id: str,
        auth_method: str,
        csrf_secret: str,
        user_agent_hash: str | None,
        sliding_seconds: int,
        absolute_seconds: int,
        own_sliding_seconds: int | None = None,
    ) -> SessionRecord:
        """``own_sliding_seconds`` gives this session its own idle lifetime ("stay signed in")."""
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_auth_sessions
                (id, user_id, workspace_id, auth_method, csrf_secret, user_agent_hash,
                 expires_at, absolute_expires_at, sliding_seconds)
            VALUES (%s, %s, %s, %s, %s, %s,
                    CURRENT_TIMESTAMP + make_interval(secs => %s),
                    CURRENT_TIMESTAMP + make_interval(secs => %s), %s)
            RETURNING {_SESSION_COLUMNS}
            """,
            (
                session_id,
                user_id,
                workspace_id,
                auth_method,
                csrf_secret,
                user_agent_hash,
                min(own_sliding_seconds or sliding_seconds, absolute_seconds),
                absolute_seconds,
                own_sliding_seconds,
            ),
        ).fetchone()
        return _session(row)

    def active_session(self, session_id: str, *, sliding_seconds: int) -> SessionRecord | None:
        row = self.connection.execute(
            f"""
            SELECT {_SESSION_COLUMNS},
                   last_seen_at < CURRENT_TIMESTAMP - make_interval(secs => %s)
              FROM omnix_auth_sessions
             WHERE id = %s
               AND revoked_at IS NULL
               AND expires_at > CURRENT_TIMESTAMP
               AND absolute_expires_at > CURRENT_TIMESTAMP
            """,
            (_TOUCH_INTERVAL_SECONDS, session_id),
        ).fetchone()
        if row is None:
            return None
        if not bool(row[7]):
            return _session(row)
        touched = self.connection.execute(
            f"""
            UPDATE omnix_auth_sessions
               SET last_seen_at = CURRENT_TIMESTAMP,
                   expires_at = LEAST(
                       absolute_expires_at,
                       CURRENT_TIMESTAMP + make_interval(secs => COALESCE(sliding_seconds, %s))
                   )
             WHERE id = %s AND revoked_at IS NULL
            RETURNING {_SESSION_COLUMNS}
            """,
            (sliding_seconds, session_id),
        ).fetchone()
        return _session(touched) if touched is not None else None

    def revoke_session(self, session_id: str) -> bool:
        row = self.connection.execute(
            """
            UPDATE omnix_auth_sessions
               SET revoked_at = CURRENT_TIMESTAMP
             WHERE id = %s AND revoked_at IS NULL
            RETURNING id
            """,
            (session_id,),
        ).fetchone()
        return row is not None

    def user_sessions(self, user_id: str) -> list[tuple[str, SessionSummary]]:
        rows = self.connection.execute(
            """
            SELECT id, auth_method, created_at, last_seen_at, LEAST(expires_at, absolute_expires_at)
              FROM omnix_auth_sessions
             WHERE user_id = %s
               AND revoked_at IS NULL
               AND expires_at > CURRENT_TIMESTAMP
               AND absolute_expires_at > CURRENT_TIMESTAMP
             ORDER BY last_seen_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [
            (str(row[0]), SessionSummary(handle=str(row[0])[:SESSION_HANDLE_LENGTH], auth_method=str(row[1]),
                                         created_at=row[2], last_seen_at=row[3], expires_at=row[4]))
            for row in rows
        ]

    def session_method_and_age(self, session_id: str) -> tuple[str, float] | None:
        """The stored session's sign-in method and age in seconds."""
        row = self.connection.execute(
            "SELECT auth_method, EXTRACT(EPOCH FROM CURRENT_TIMESTAMP - created_at) FROM omnix_auth_sessions WHERE id = %s",
            (session_id,),
        ).fetchone()
        return (str(row[0]), float(row[1])) if row else None

    def session_age_seconds(self, session_id: str) -> float | None:
        row = self.connection.execute(
            "SELECT EXTRACT(EPOCH FROM CURRENT_TIMESTAMP - created_at) FROM omnix_auth_sessions WHERE id = %s",
            (session_id,),
        ).fetchone()
        return float(row[0]) if row else None

    def revoke_user_sessions_matching(self, user_id: str, *, handle: str | None, keep: str) -> int:
        """Revoke one of the user's sessions by handle, or all but ``keep``."""
        if handle is not None:
            rows = self.connection.execute(
                """
                UPDATE omnix_auth_sessions
                   SET revoked_at = CURRENT_TIMESTAMP
                 WHERE user_id = %s AND revoked_at IS NULL AND left(id, %s) = %s
                RETURNING id
                """,
                (user_id, SESSION_HANDLE_LENGTH, handle),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                UPDATE omnix_auth_sessions
                   SET revoked_at = CURRENT_TIMESTAMP
                 WHERE user_id = %s AND revoked_at IS NULL AND id <> %s
                RETURNING id
                """,
                (user_id, keep),
            ).fetchall()
        return len(rows)

    def revoke_user_sessions(self, user_id: str) -> int:
        rows = self.connection.execute(
            """
            UPDATE omnix_auth_sessions
               SET revoked_at = CURRENT_TIMESTAMP
             WHERE user_id = %s AND revoked_at IS NULL
            RETURNING id
            """,
            (user_id,),
        ).fetchall()
        return len(rows)

    # Install credential ---------------------------------------------------

    def install_credential(self) -> InstallCredentialRecord | None:
        row = self.connection.execute(
            """
            SELECT user_id, workspace_id, salt, credential_hash, scrypt_n, scrypt_r, scrypt_p
              FROM omnix_install_credentials
             WHERE id = 'install'
            """
        ).fetchone()
        if row is None:
            return None
        return InstallCredentialRecord(
            user_id=str(row[0]),
            workspace_id=str(row[1]),
            salt=bytes(row[2]),
            credential_hash=bytes(row[3]),
            n=int(row[4]),
            r=int(row[5]),
            p=int(row[6]),
        )

    def store_install_credential(
        self,
        *,
        user_id: str,
        workspace_id: str,
        salt: bytes,
        credential_hash: bytes,
        n: int,
        r: int,
        p: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO omnix_install_credentials
                (id, user_id, workspace_id, salt, credential_hash, scrypt_n, scrypt_r, scrypt_p)
            VALUES ('install', %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
               SET user_id = EXCLUDED.user_id,
                   workspace_id = EXCLUDED.workspace_id,
                   salt = EXCLUDED.salt,
                   credential_hash = EXCLUDED.credential_hash,
                   scrypt_n = EXCLUDED.scrypt_n,
                   scrypt_r = EXCLUDED.scrypt_r,
                   scrypt_p = EXCLUDED.scrypt_p,
                   rotated_at = CURRENT_TIMESTAMP
            """,
            (user_id, workspace_id, salt, credential_hash, n, r, p),
        )

    # Launcher login codes -------------------------------------------------

    def insert_login_code(
        self, *, code_hash: str, user_id: str, workspace_id: str, ttl_seconds: int
    ) -> None:
        self.connection.execute(
            "DELETE FROM omnix_auth_login_codes WHERE expires_at < CURRENT_TIMESTAMP - INTERVAL '1 hour'"
        )
        self.connection.execute(
            """
            INSERT INTO omnix_auth_login_codes (code_hash, user_id, workspace_id, expires_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP + make_interval(secs => %s))
            """,
            (code_hash, user_id, workspace_id, ttl_seconds),
        )

    def consume_login_code(self, code_hash: str) -> tuple[str, str] | None:
        row = self.connection.execute(
            """
            UPDATE omnix_auth_login_codes
               SET consumed_at = CURRENT_TIMESTAMP
             WHERE code_hash = %s
               AND consumed_at IS NULL
               AND expires_at > CURRENT_TIMESTAMP
            RETURNING user_id, workspace_id
            """,
            (code_hash,),
        ).fetchone()
        return (str(row[0]), str(row[1])) if row is not None else None

    # OIDC -----------------------------------------------------------------

    def insert_oidc_state(
        self,
        *,
        state_hash: str,
        browser_binding_hash: str,
        nonce: str,
        code_verifier: str,
        redirect_after: str,
        ttl_seconds: int,
        purpose: str = "oidc",
        link_user_id: str | None = None,
        invite_token_hash: str | None = None,
        remember: bool = False,
    ) -> None:
        self.connection.execute(
            "DELETE FROM omnix_oidc_login_states WHERE expires_at < CURRENT_TIMESTAMP - INTERVAL '1 hour'"
        )
        self.connection.execute(
            """
            INSERT INTO omnix_oidc_login_states
                (state_hash, browser_binding_hash, nonce, code_verifier, redirect_after, expires_at,
                 purpose, link_user_id, invite_token_hash, remember)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP + make_interval(secs => %s), %s, %s, %s, %s)
            """,
            (state_hash, browser_binding_hash, nonce, code_verifier, redirect_after, ttl_seconds,
             purpose, link_user_id, invite_token_hash, remember),
        )

    def consume_oidc_state(
        self, *, state_hash: str, browser_binding_hash: str | Sequence[str]
    ) -> OidcLoginState | None:
        """Use a login state once; ``browser_binding_hash`` may list each binding cookie the browser sent."""
        bindings = [browser_binding_hash] if isinstance(browser_binding_hash, str) else list(browser_binding_hash)
        row = self.connection.execute(
            """
            UPDATE omnix_oidc_login_states
               SET consumed_at = CURRENT_TIMESTAMP
             WHERE state_hash = %s
               AND browser_binding_hash = ANY(%s)
               AND consumed_at IS NULL
               AND expires_at > CURRENT_TIMESTAMP
            RETURNING nonce, code_verifier, redirect_after, purpose, link_user_id, invite_token_hash, remember
            """,
            (state_hash, bindings),
        ).fetchone()
        if row is None:
            return None
        return OidcLoginState(
            nonce=str(row[0]), code_verifier=str(row[1]), redirect_after=str(row[2]), purpose=str(row[3]),
            link_user_id=str(row[4]) if row[4] is not None else None,
            invite_token_hash=str(row[5]) if row[5] is not None else None,
            remember=bool(row[6]),
        )

    # Desktop Google sign-in handoffs (MarketPulse S-5) --------------------

    def insert_desktop_handoff(
        self,
        *,
        nonce_hash: str,
        begin_token_hash: str,
        purpose: str,
        link_user_id: str | None,
        invite_token_hash: str | None,
        remember: bool,
        redirect_after: str,
        ttl_seconds: int,
    ) -> None:
        self.connection.execute(
            "DELETE FROM omnix_auth_desktop_handoffs WHERE expires_at < CURRENT_TIMESTAMP - INTERVAL '1 hour'"
        )
        self.connection.execute(
            """
            INSERT INTO omnix_auth_desktop_handoffs
                (nonce_hash, begin_token_hash, purpose, link_user_id, invite_token_hash, remember, redirect_after,
                 expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP + make_interval(secs => %s))
            """,
            (nonce_hash, begin_token_hash, purpose, link_user_id, invite_token_hash, remember, redirect_after,
             ttl_seconds),
        )

    def begin_desktop_handoff(self, begin_token_hash: str) -> DesktopHandoff | None:
        """Opens a handoff's begin link once, while it is valid."""
        row = self.connection.execute(
            """
            UPDATE omnix_auth_desktop_handoffs
               SET begun_at = CURRENT_TIMESTAMP
             WHERE begin_token_hash = %s AND begun_at IS NULL AND expires_at > CURRENT_TIMESTAMP
            RETURNING nonce_hash, purpose, link_user_id, invite_token_hash, remember, redirect_after
            """,
            (begin_token_hash,),
        ).fetchone()
        if row is None:
            return None
        return DesktopHandoff(nonce_hash=str(row[0]), purpose=str(row[1]),
                              link_user_id=str(row[2]) if row[2] is not None else None,
                              invite_token_hash=str(row[3]) if row[3] is not None else None,
                              remember=bool(row[4]), redirect_after=str(row[5]))

    def attach_desktop_state(self, nonce_hash: str, state_hash: str) -> None:
        self.connection.execute(
            "UPDATE omnix_auth_desktop_handoffs SET state_hash = %s WHERE nonce_hash = %s",
            (state_hash, nonce_hash),
        )

    def complete_desktop_handoff(
        self, state_hash: str, *, user_id: str | None = None, workspace_id: str | None = None, error: str | None = None
    ) -> bool:
        """Records the Google result on the handoff that started this login state."""
        row = self.connection.execute(
            """
            UPDATE omnix_auth_desktop_handoffs
               SET user_id = %s, workspace_id = %s, error = %s, completed_at = CURRENT_TIMESTAMP
             WHERE state_hash = %s AND completed_at IS NULL AND expires_at > CURRENT_TIMESTAMP
            RETURNING nonce_hash
            """,
            (user_id, workspace_id, error, state_hash),
        ).fetchone()
        return row is not None

    def take_desktop_handoff(self, nonce_hash: str) -> DesktopHandoffResult | None:
        """The handoff's outcome for the window holding its nonce: used once when complete, else still pending."""
        row = self.connection.execute(
            """
            UPDATE omnix_auth_desktop_handoffs
               SET consumed_at = CURRENT_TIMESTAMP
             WHERE nonce_hash = %s AND consumed_at IS NULL AND completed_at IS NOT NULL
               AND expires_at > CURRENT_TIMESTAMP
            RETURNING purpose, user_id, workspace_id, error, remember, redirect_after
            """,
            (nonce_hash,),
        ).fetchone()
        if row is not None:
            return DesktopHandoffResult(
                pending=False, purpose=str(row[0]), user_id=str(row[1]) if row[1] is not None else None,
                workspace_id=str(row[2]) if row[2] is not None else None,
                error=str(row[3]) if row[3] is not None else None, remember=bool(row[4]), redirect_after=str(row[5]),
            )
        waiting = self.connection.execute(
            """
            SELECT purpose, remember, redirect_after FROM omnix_auth_desktop_handoffs
             WHERE nonce_hash = %s AND consumed_at IS NULL AND completed_at IS NULL AND expires_at > CURRENT_TIMESTAMP
            """,
            (nonce_hash,),
        ).fetchone()
        if waiting is None:
            return None
        return DesktopHandoffResult(pending=True, purpose=str(waiting[0]), user_id=None, workspace_id=None, error=None,
                                    remember=bool(waiting[1]), redirect_after=str(waiting[2]))

    def external_identity_user(self, *, issuer: str, subject: str) -> str | None:
        row = self.connection.execute(
            "SELECT user_id FROM omnix_external_identities WHERE issuer = %s AND subject = %s",
            (issuer, subject),
        ).fetchone()
        return str(row[0]) if row is not None else None

    def link_external_identity(
        self, *, issuer: str, subject: str, user_id: str, email: str | None
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO omnix_external_identities (issuer, subject, user_id, email)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (issuer, subject) DO UPDATE
               SET email = EXCLUDED.email, last_login_at = CURRENT_TIMESTAMP
            """,
            (issuer, subject, user_id, email),
        )

    # Accounts -------------------------------------------------------------

    def has_password(self, user_id: str) -> bool:
        row = self.connection.execute("SELECT 1 FROM omnix_user_passwords WHERE user_id = %s", (user_id,)).fetchone()
        return row is not None

    def linked_issuers(self, user_id: str) -> tuple[str, ...]:
        rows = self.connection.execute(
            "SELECT DISTINCT issuer FROM omnix_external_identities WHERE user_id = %s ORDER BY issuer",
            (user_id,),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def password(self, user_id: str) -> PasswordRecord | None:
        row = self.connection.execute(
            """
            SELECT user_id, salt, credential_hash, scrypt_n, scrypt_r, scrypt_p
              FROM omnix_user_passwords WHERE user_id = %s
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return PasswordRecord(user_id=str(row[0]), salt=bytes(row[1]), credential_hash=bytes(row[2]),
                              n=int(row[3]), r=int(row[4]), p=int(row[5]))

    def store_password(self, *, user_id: str, salt: bytes, credential_hash: bytes, n: int, r: int, p: int) -> None:
        self.connection.execute(
            """
            INSERT INTO omnix_user_passwords (user_id, salt, credential_hash, scrypt_n, scrypt_r, scrypt_p)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE
               SET salt = EXCLUDED.salt, credential_hash = EXCLUDED.credential_hash,
                   scrypt_n = EXCLUDED.scrypt_n, scrypt_r = EXCLUDED.scrypt_r, scrypt_p = EXCLUDED.scrypt_p,
                   updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, salt, credential_hash, n, r, p),
        )

    # Invites --------------------------------------------------------------

    def insert_invite(self, *, token_hash: str, created_by: str, note: str | None, ttl_seconds: int) -> datetime:
        row = self.connection.execute(
            """
            INSERT INTO omnix_auth_invites (token_hash, created_by, note, expires_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP + make_interval(secs => %s))
            RETURNING expires_at
            """,
            (token_hash, created_by, note, ttl_seconds),
        ).fetchone()
        return row[0]

    def invite_usable(self, token_hash: str) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM omnix_auth_invites
             WHERE token_hash = %s AND consumed_at IS NULL AND expires_at > CURRENT_TIMESTAMP
            """,
            (token_hash,),
        ).fetchone()
        return row is not None

    def consume_invite(self, token_hash: str, *, consumed_by: str) -> bool:
        row = self.connection.execute(
            """
            UPDATE omnix_auth_invites
               SET consumed_at = CURRENT_TIMESTAMP, consumed_by = %s
             WHERE token_hash = %s AND consumed_at IS NULL AND expires_at > CURRENT_TIMESTAMP
            RETURNING token_hash
            """,
            (consumed_by, token_hash),
        ).fetchone()
        return row is not None

    def open_invites(self, created_by: str) -> list[InviteSummary]:
        rows = self.connection.execute(
            """
            SELECT token_hash, note, created_at, expires_at FROM omnix_auth_invites
             WHERE created_by = %s AND consumed_at IS NULL AND expires_at > CURRENT_TIMESTAMP
             ORDER BY created_at DESC
             LIMIT 100
            """,
            (created_by,),
        ).fetchall()
        return [InviteSummary(handle=str(row[0])[:SESSION_HANDLE_LENGTH], note=row[1], created_at=row[2],
                              expires_at=row[3]) for row in rows]

    def revoke_invite(self, *, created_by: str, handle: str) -> bool:
        row = self.connection.execute(
            """
            UPDATE omnix_auth_invites SET expires_at = CURRENT_TIMESTAMP
             WHERE created_by = %s AND consumed_at IS NULL AND left(token_hash, %s) = %s
               AND expires_at > CURRENT_TIMESTAMP
            RETURNING token_hash
            """,
            (created_by, SESSION_HANDLE_LENGTH, handle),
        ).fetchone()
        return row is not None
