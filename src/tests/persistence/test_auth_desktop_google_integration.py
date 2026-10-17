"""Google sign-in for the desktop app through the system browser (MarketPulse S-5)."""
from __future__ import annotations

import dataclasses
import os
import secrets
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.persistence.config import DatabaseSettings
from app.persistence.database import PostgresDatabase
from app.persistence.identity_service import ensure_local_identity
from app.security.auth import AuthService, resolve_auth_settings
from app.security.auth.routes import GOOGLE_DESKTOP_TX_COOKIE, create_auth_router
from app.security.auth.service import AccountError
from tests.support.fake_oidc import FakeIdentityProvider, oidc_settings

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("OMNIX_TEST_DATABASE_URL"),
        reason="OMNIX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
    ),
]

PASSWORD = "a long enough passphrase"


@pytest.fixture
def database():
    db = PostgresDatabase(DatabaseSettings(url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=3,
                                           application_name="omnix-desktop-google-tests"))
    ensure_local_identity(db)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def idp():
    return FakeIdentityProvider()


def _service(database, idp, *, desktop: bool = True, registration: str = "open") -> AuthService:
    settings = resolve_auth_settings({"OMNIX_AUTH_MODE": "local"})
    accounts = dataclasses.replace(settings.accounts, google=oidc_settings(), desktop_handoff=desktop,
                                   registration=registration)
    return AuthService(dataclasses.replace(settings, accounts=accounts), database=database, google=idp.client())


def _claims() -> dict:
    token = uuid.uuid4().hex[:12]
    return {"sub": f"google-{token}", "email": f"g-{token}@example.com", "email_verified": True, "name": "Grace"}


def _google(service, idp, begin_token, claims):
    url, binding = service.begin_desktop_google(begin_token)
    code, state = idp.authorize(url, claims)
    return service.complete_google_login(code=code, state=state, browser_binding=["", binding], user_agent=None)


def _age_handoffs(database) -> None:
    with database.transaction() as connection:
        connection.execute("SELECT set_config('omnix.system', 'on', true)")
        connection.execute("UPDATE omnix_auth_desktop_handoffs SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second'")


def test_the_window_gets_the_session_and_the_system_browser_does_not(database, idp) -> None:
    service = _service(database, idp)
    nonce, begin_token = service.start_desktop_google(redirect_after="/trading")
    assert service.take_desktop_handoff(nonce, user_agent=None).status == "pending"

    issued, target = _google(service, idp, begin_token, _claims())

    assert (issued, target) == (None, "desktop:done")
    outcome = service.take_desktop_handoff(nonce, user_agent="pytest")
    assert outcome.status == "signed_in" and outcome.redirect_after == "/trading"
    assert service.authenticate_session(outcome.session.token) is not None
    assert outcome.session.principal.auth_method == "session:google"
    # Used once.
    assert service.take_desktop_handoff(nonce, user_agent=None) is None


def test_the_begin_link_opens_once_and_a_foreign_nonce_gets_nothing(database, idp) -> None:
    service = _service(database, idp)
    nonce, begin_token = service.start_desktop_google(redirect_after="/")

    assert service.begin_desktop_google(begin_token) is not None
    assert service.begin_desktop_google(begin_token) is None
    assert service.take_desktop_handoff(secrets.token_urlsafe(32), user_agent=None) is None
    assert service.take_desktop_handoff(nonce, user_agent=None).status == "pending"


def test_an_expired_handoff_gives_nothing(database, idp) -> None:
    service = _service(database, idp)
    nonce, begin_token = service.start_desktop_google(redirect_after="/")
    _google(service, idp, begin_token, _claims())
    _age_handoffs(database)

    assert service.take_desktop_handoff(nonce, user_agent=None) is None


def test_a_refused_sign_in_is_reported_to_the_window(database, idp) -> None:
    service = _service(database, idp, registration="closed")
    nonce, begin_token = service.start_desktop_google(redirect_after="/")

    issued, target = _google(service, idp, begin_token, _claims())

    assert issued is None and target == "desktop:error:registration_closed"
    outcome = service.take_desktop_handoff(nonce, user_agent=None)
    assert (outcome.status, outcome.error) == ("failed", "registration_closed")


def test_linking_google_from_the_desktop_reports_linked(database, idp) -> None:
    service = _service(database, idp)
    email = f"user-{uuid.uuid4().hex[:12]}@example.com"
    account = service.register(email=email, password=PASSWORD, display_name="Ada", invite=None, remember=False,
                               user_agent="pytest")
    nonce, begin_token = service.start_desktop_google(redirect_after="/settings", link_user=account.principal.user_id)

    assert _google(service, idp, begin_token, _claims()) == (None, "desktop:done")
    outcome = service.take_desktop_handoff(nonce, user_agent=None)
    assert outcome.status == "linked" and outcome.session is None
    assert service.account_view(account.principal).google_linked is True


def test_the_browser_flow_still_works_with_a_leftover_desktop_cookie(database, idp) -> None:
    service = _service(database, idp)
    url, binding = service.start_google_login(redirect_after="/chat")
    code, state = idp.authorize(url, _claims())

    issued, target = service.complete_google_login(code=code, state=state,
                                                   browser_binding=[binding, "stale-desktop-binding"], user_agent=None)

    assert issued is not None and target == "/chat"


def test_desktop_sign_in_is_off_unless_configured(database, idp) -> None:
    service = _service(database, idp, desktop=False)

    with pytest.raises(AccountError) as refused:
        service.start_desktop_google(redirect_after="/")
    assert refused.value.status == 404


def test_the_routes_keep_the_nonce_in_the_window_and_sign_the_window_in(database, idp) -> None:
    service = _service(database, idp)
    app = FastAPI()
    app.include_router(create_auth_router(lambda: service))
    window = TestClient(app, base_url="http://127.0.0.1")
    browser = TestClient(app, base_url="http://127.0.0.1")

    assert window.get("/api/auth/google/desktop/start").status_code == 405
    started = window.post("/api/auth/google/desktop/start", json={"next": "/trading"}).json()
    nonce, begin_url = started["nonce"], started["begin_url"]
    assert nonce not in begin_url
    assert window.post("/api/auth/google/desktop/handoff", json={"nonce": nonce}).status_code == 202

    begun = browser.get(begin_url, follow_redirects=False)
    assert begun.status_code == 302 and GOOGLE_DESKTOP_TX_COOKIE in begun.cookies
    code, state = idp.authorize(begun.headers["location"], _claims())
    page = browser.get(f"/api/auth/google/callback?code={code}&state={state}", follow_redirects=False)
    assert page.status_code == 200 and "Return to" in page.text
    assert "omnix_session" not in browser.cookies and "__Host-omnix_session" not in browser.cookies

    handed = window.post("/api/auth/google/desktop/handoff", json={"nonce": nonce})
    assert handed.status_code == 200 and handed.json() == {"status": "signed_in", "next": "/trading"}
    assert "omnix_session" in window.cookies
    assert window.get("/api/auth/session").json()["authenticated"] is True
    assert window.post("/api/auth/google/desktop/handoff", json={"nonce": nonce}).status_code == 410
    # The begin link is spent.
    assert browser.get(begin_url, follow_redirects=False).status_code == 400
