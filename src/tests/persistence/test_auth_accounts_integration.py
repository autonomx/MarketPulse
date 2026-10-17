"""Accounts in local mode: registration, password and guest sign-in, invites,
"stay signed in", password changes and Google (WP-4.1)."""
from __future__ import annotations

import dataclasses
import os
import secrets
import uuid

import pytest
from fastapi.testclient import TestClient

from app.persistence.config import DatabaseSettings
from app.persistence.database import PostgresDatabase
from app.persistence.identity_service import ensure_local_identity
from app.security.auth import AuthService, resolve_auth_settings
from app.security.auth.service import AccountError, ReauthenticationRequired
from tests.support.fake_oidc import FakeIdentityProvider, oidc_settings

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.xdist_group("auth-install-credential"),
    pytest.mark.skipif(
        not os.environ.get("OMNIX_TEST_DATABASE_URL"),
        reason="OMNIX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
    ),
]

PASSWORD = "correct horse battery staple"


@pytest.fixture
def database():
    db = PostgresDatabase(DatabaseSettings(url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=3,
                                           application_name="omnix-account-tests"))
    ensure_local_identity(db)
    try:
        yield db
    finally:
        db.close()


def _service(database, *, idp: FakeIdentityProvider | None = None, **policy) -> AuthService:
    settings = resolve_auth_settings({"OMNIX_AUTH_MODE": "local"})
    accounts = dataclasses.replace(settings.accounts, google=oidc_settings() if idp else None, **policy)
    return AuthService(dataclasses.replace(settings, accounts=accounts), database=database,
                       google=idp.client() if idp else None)


def _email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@Example.com"


def _session_lifetime(database, token_session_id: str) -> float:
    # Sessions are row-level secured: the test reads as the system, not as a tenant.
    with database.transaction() as connection:
        connection.execute("SELECT set_config('omnix.system', 'on', true)")
        return float(connection.execute(
            "SELECT EXTRACT(EPOCH FROM absolute_expires_at - created_at) FROM omnix_auth_sessions WHERE id = %s",
            (token_session_id,),
        ).fetchone()[0])


def test_register_then_sign_in_with_a_password(database) -> None:
    service = _service(database)
    email = _email()
    issued = service.register(email=email, password=PASSWORD, display_name="Ada", invite=None,
                              remember=False, user_agent="pytest")
    context = issued.principal.context
    # A workspace of their own, where they are the owner.
    assert context.workspace_id != "workspace:local"
    assert {"owner", "admin", "member"} <= context.roles
    assert service.authenticate_session(issued.token) is not None
    account = service.account_view(issued.principal)
    assert account is not None and (account.email, account.kind, account.has_password) == (email.lower(), "standard", True)

    with pytest.raises(AccountError, match="email_taken"):
        service.register(email=email.upper(), password=PASSWORD, display_name=None, invite=None, remember=False,
                         user_agent=None)
    with pytest.raises(AccountError, match="password_too_short"):
        service.register(email=_email(), password="short", display_name=None, invite=None, remember=False,
                         user_agent=None)
    twin = _email()
    with pytest.raises(AccountError, match="password_is_email"):
        service.register(email=twin, password=twin.lower(), display_name=None, invite=None, remember=False,
                         user_agent=None)

    signed_in = service.login_with_password(email=email.upper(), password=PASSWORD, remember=False, user_agent=None)
    assert signed_in is not None and signed_in.principal.user_id == issued.principal.user_id
    assert signed_in.principal.context.workspace_id == context.workspace_id
    # A wrong password and an unknown email look the same.
    assert service.login_with_password(email=email, password="wrong password!", remember=False, user_agent=None) is None
    assert service.login_with_password(email=_email(), password=PASSWORD, remember=False, user_agent=None) is None


def test_a_new_workspace_starts_with_the_installation_settings(database) -> None:
    key = f"seed-{uuid.uuid4().hex[:8]}"
    with database.transaction() as connection:
        connection.execute("SELECT set_config('omnix.system', 'on', true)")
        connection.execute(
            "INSERT INTO omnix_settings (workspace_id, setting_scope, setting_key, value) VALUES ('workspace:local', 'test', %s, '\"lmstudio\"'::jsonb)",
            (key,),
        )
    issued = _service(database).register(email=_email(), password=PASSWORD, display_name=None, invite=None,
                                         remember=False, user_agent=None)
    with database.transaction() as connection:
        connection.execute("SELECT set_config('omnix.system', 'on', true)")
        value = connection.execute(
            "SELECT value FROM omnix_settings WHERE workspace_id = %s AND setting_key = %s",
            (issued.principal.context.workspace_id, key),
        ).fetchone()
        connection.execute("DELETE FROM omnix_settings WHERE setting_key = %s", (key,))
    assert value is not None and value[0] == "lmstudio"


def test_registration_policy_and_single_use_invites(database) -> None:
    open_service = _service(database)
    inviter = open_service.register(email=_email(), password=PASSWORD, display_name=None, invite=None,
                                    remember=False, user_agent=None).principal

    closed = _service(database, registration="closed")
    with pytest.raises(AccountError, match="registration_closed"):
        closed.register(email=_email(), password=PASSWORD, display_name=None, invite=None, remember=False, user_agent=None)

    invite_only = _service(database, registration="invite")
    with pytest.raises(AccountError, match="invite_required"):
        invite_only.register(email=_email(), password=PASSWORD, display_name=None, invite=None, remember=False,
                             user_agent=None)
    invite = invite_only.create_invite(inviter, note="for a friend").token
    assert invite_only.invite_status(invite) is True
    assert [item.note for item in invite_only.list_invites(inviter)][0] == "for a friend"
    joined = invite_only.register(email=_email(), password=PASSWORD, display_name=None, invite=invite,
                                  remember=False, user_agent=None)
    assert joined.principal.context.workspace_id != inviter.context.workspace_id
    # Spent: it opens the door once.
    assert invite_only.invite_status(invite) is False
    with pytest.raises(AccountError, match="invite_invalid"):
        invite_only.register(email=_email(), password=PASSWORD, display_name=None, invite=invite, remember=False,
                             user_agent=None)
    # A revoked invite opens nothing.
    revoked = invite_only.create_invite(inviter, note=None)
    handle = invite_only.list_invites(inviter)[0].handle
    assert invite_only.revoke_invite(inviter, handle) is True
    assert invite_only.invite_status(revoked.token) is False


def test_stay_signed_in_lasts_longer(database) -> None:
    service = _service(database)
    email = _email()
    service.register(email=email, password=PASSWORD, display_name=None, invite=None, remember=False, user_agent=None)
    short = service.login_with_password(email=email, password=PASSWORD, remember=False, user_agent=None)
    long = service.login_with_password(email=email, password=PASSWORD, remember=True, user_agent=None)
    assert short is not None and long is not None
    assert _session_lifetime(database, short.principal.session_id) == pytest.approx(service.settings.absolute_ttl_seconds, abs=5)
    assert _session_lifetime(database, long.principal.session_id) == pytest.approx(
        service.settings.accounts.remember_ttl_seconds, abs=5)
    assert long.max_age_seconds == service.settings.accounts.remember_ttl_seconds


def test_a_guest_becomes_a_full_account_and_keeps_its_workspace(database) -> None:
    service = _service(database, guests=True)
    guest = service.login_as_guest(user_agent="pytest")
    assert guest.principal.context.roles == frozenset({"guest"})
    assert _session_lifetime(database, guest.principal.session_id) == pytest.approx(
        service.settings.accounts.guest_ttl_seconds, abs=5)
    assert service.account_view(guest.principal).kind == "guest"
    with pytest.raises(AccountError, match="guest_must_sign_up"):
        service.change_password(guest.principal, current=None, new_password=PASSWORD)

    email = _email()
    upgraded = service.upgrade_guest(guest.principal, email=email, password=PASSWORD, display_name="Grace",
                                     remember=True, user_agent=None)
    assert upgraded.principal.user_id == guest.principal.user_id
    assert upgraded.principal.context.workspace_id == guest.principal.context.workspace_id
    assert {"owner", "admin", "member"} <= upgraded.principal.context.roles
    # The guest session ended; the new one and a password sign-in work.
    assert service.authenticate_session(guest.token) is None
    assert service.authenticate_session(upgraded.token) is not None
    assert service.login_with_password(email=email, password=PASSWORD, remember=False, user_agent=None) is not None
    with pytest.raises(AccountError, match="not_a_guest"):
        service.upgrade_guest(upgraded.principal, email=_email(), password=PASSWORD, display_name=None,
                              remember=False, user_agent=None)

    with pytest.raises(AccountError, match="guests_disabled"):
        _service(database, guests=False).login_as_guest(user_agent=None)


def test_changing_a_password_needs_the_old_one_and_signs_out_other_devices(database) -> None:
    service = _service(database)
    email = _email()
    first = service.register(email=email, password=PASSWORD, display_name=None, invite=None, remember=False,
                             user_agent=None)
    other = service.login_with_password(email=email, password=PASSWORD, remember=False, user_agent=None)
    assert other is not None
    with pytest.raises(ReauthenticationRequired):
        service.change_password(first.principal, current="not my password", new_password="a brand new passphrase")
    assert service.change_password(first.principal, current=PASSWORD, new_password="a brand new passphrase") == 1
    assert service.authenticate_session(other.token) is None
    assert service.authenticate_session(first.token) is not None
    assert service.login_with_password(email=email, password=PASSWORD, remember=False, user_agent=None) is None
    assert service.login_with_password(email=email, password="a brand new passphrase", remember=False,
                                       user_agent=None) is not None


def _google(service: AuthService, idp: FakeIdentityProvider, claims: dict, **start):
    url, binding = service.start_google_login(redirect_after="/chat", **start)
    code, state = idp.authorize(url, claims)
    return service.complete_google_login(code=code, state=state, browser_binding=binding, user_agent=None)


def test_google_sign_in_creates_then_finds_the_account(database) -> None:
    idp = FakeIdentityProvider()
    service = _service(database, idp=idp)
    email = _email().lower()
    claims = {"sub": f"google-{uuid.uuid4().hex}", "email": email, "email_verified": True, "name": "Lin"}
    issued, target = _google(service, idp, claims)
    assert issued is not None and target == "/chat"
    account = service.account_view(issued.principal)
    assert account is not None and (account.email, account.google_linked, account.has_password) == (email, True, False)
    again, _ = _google(service, idp, claims)
    assert again is not None and again.principal.user_id == issued.principal.user_id


def test_google_never_takes_over_an_account_by_email(database) -> None:
    idp = FakeIdentityProvider()
    service = _service(database, idp=idp)
    email = _email().lower()
    existing = service.register(email=email, password=PASSWORD, display_name=None, invite=None, remember=False,
                                user_agent=None)
    claims = {"sub": f"google-{uuid.uuid4().hex}", "email": email, "email_verified": True}
    issued, target = _google(service, idp, claims)
    assert issued is None and target == "/login?error=google_email_in_use"

    # The account's owner links Google from their settings instead.
    linked, target = _google(service, idp, claims, link_user=existing.principal.user_id)
    assert linked is None and target == "/chat"
    signed_in, _ = _google(service, idp, claims)
    assert signed_in is not None and signed_in.principal.user_id == existing.principal.user_id
    # The same Google account cannot be linked to a second user.
    other = service.register(email=_email(), password=PASSWORD, display_name=None, invite=None, remember=False,
                             user_agent=None)
    refused, target = _google(service, idp, claims, link_user=other.principal.user_id)
    assert refused is None and "google_linked_elsewhere" in target


def test_google_sign_up_follows_the_registration_policy(database) -> None:
    idp = FakeIdentityProvider()
    service = _service(database, idp=idp, registration="invite")
    claims = {"sub": f"google-{uuid.uuid4().hex}", "email": _email().lower(), "email_verified": True}
    issued, target = _google(service, idp, claims)
    assert issued is None and target == "/login?error=invite_required"
    inviter = _service(database).register(email=_email(), password=PASSWORD, display_name=None, invite=None,
                                          remember=False, user_agent=None).principal
    invite = service.create_invite(inviter, note=None).token
    issued, _ = _google(service, idp, claims, invite=invite)
    assert issued is not None
    assert service.invite_status(invite) is False


def test_sign_up_and_guest_flows_through_the_gateway(database) -> None:
    from app.composition.gateway.main import create_gateway_app

    service = _service(database, guests=True)
    service.sync_install_credential(secrets.token_urlsafe(32))
    client = TestClient(create_gateway_app(auth_service=service), base_url="http://127.0.0.1",
                        headers={"X-Omnix-Client": "test"}, follow_redirects=False)
    anonymous = client.get("/api/auth/session").json()
    assert anonymous["authenticated"] is False
    assert anonymous["options"] == {"registration": "open", "guests": True, "google": False, "google_desktop": False,
                                    "install_credential": True, "min_password_length": 12}

    assert client.post("/api/auth/register", json={"email": "not-an-email", "password": PASSWORD}).json()["detail"] == "invalid_email"
    email = _email()
    registered = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "display_name": "Mo"})
    assert registered.status_code == 201
    body = registered.json()
    assert body["authenticated"] is True and body["account"]["display_name"] == "Mo"
    assert body["account"]["kind"] == "standard" and body["account"]["is_owner"] is False
    csrf = client.cookies.get("omnix_csrf")
    # An owner of their own workspace may invite (admin:users there).
    invite = client.post("/api/auth/invites", json={"note": "sam"}, headers={"X-Omnix-CSRF": csrf})
    assert invite.status_code == 201 and invite.json()["path"].startswith("/login?invite=")
    assert client.get("/api/auth/invites/check", params={"invite": invite.json()["invite"]}).json() == {"usable": True}
    assert client.post("/api/auth/logout", headers={"X-Omnix-CSRF": csrf}).status_code == 204
    client.cookies.clear()

    assert client.post("/api/auth/password/login", json={"email": email, "password": "wrong password!"}).status_code == 401
    assert client.post("/api/auth/password/login", json={"email": email, "password": PASSWORD}).status_code == 200
    client.cookies.clear()

    # Another device: sign-in attempts are limited per address.
    client = TestClient(create_gateway_app(auth_service=service), base_url="http://127.0.0.1",
                        headers={"X-Omnix-Client": "test"}, follow_redirects=False, client=("192.0.2.30", 50000))
    guest = client.post("/api/auth/guest")
    assert guest.status_code == 201 and guest.json()["account"]["kind"] == "guest"
    guest_csrf = client.cookies.get("omnix_csrf")
    # A guest cannot hand out invites.
    assert client.post("/api/auth/invites", json={}, headers={"X-Omnix-CSRF": guest_csrf}).status_code == 403
    upgraded = client.post("/api/auth/guest/upgrade", json={"email": _email(), "password": PASSWORD},
                           headers={"X-Omnix-CSRF": guest_csrf})
    assert upgraded.status_code == 200 and upgraded.json()["account"]["kind"] == "standard"
