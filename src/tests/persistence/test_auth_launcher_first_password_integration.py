"""The owner sets a first password from a launcher sign-in code (MarketPulse S-10)."""
from __future__ import annotations

import dataclasses
import os
import secrets

import pytest

from app.persistence.config import DatabaseSettings
from app.persistence.database import PostgresDatabase
from app.persistence.identity_service import ensure_local_identity
from app.runtime.tenant_context import LOCAL_USER_ID
from app.security.auth import AuthService, resolve_auth_settings
from app.security.auth.service import ReauthenticationRequired

# The install credential and the owner's password are singletons; run on one xdist worker with the other auth tests.
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.xdist_group("auth-install-credential"),
    pytest.mark.skipif(
        not os.environ.get("OMNIX_TEST_DATABASE_URL"),
        reason="OMNIX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
    ),
]

PASSWORD = "a long first passphrase"


@pytest.fixture
def database():
    db = PostgresDatabase(DatabaseSettings(url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=3,
                                           application_name="omnix-launcher-password-tests"))
    ensure_local_identity(db)
    try:
        yield db
    finally:
        _forget_owner_password(db)
        db.close()


def _forget_owner_password(db) -> None:
    with db.transaction() as connection:
        connection.execute("SELECT set_config('omnix.system', 'on', true)")
        connection.execute("DELETE FROM omnix_user_passwords WHERE user_id = %s", (LOCAL_USER_ID,))


def _service(database, *, launcher: bool) -> AuthService:
    settings = resolve_auth_settings({"OMNIX_AUTH_MODE": "local"})
    accounts = dataclasses.replace(settings.accounts, launcher_first_password=launcher)
    service = AuthService(dataclasses.replace(settings, accounts=accounts), database=database)
    service.sync_install_credential(secrets.token_urlsafe(32))
    _forget_owner_password(database)
    return service


def _age_sessions(database, seconds: int) -> None:
    with database.transaction() as connection:
        connection.execute("SELECT set_config('omnix.system', 'on', true)")
        connection.execute(
            "UPDATE omnix_auth_sessions SET created_at = created_at - make_interval(secs => %s) WHERE user_id = %s",
            (seconds, LOCAL_USER_ID),
        )


def test_a_fresh_launcher_session_may_set_the_first_password(database) -> None:
    service = _service(database, launcher=True)
    issued = service.exchange_login_code(service.issue_login_code(), user_agent=None)
    assert issued is not None

    service.change_password(issued.principal, current=None, new_password=PASSWORD)

    # Once a password exists, changing it needs that password again.
    with pytest.raises(ReauthenticationRequired):
        service.change_password(issued.principal, current=None, new_password="another long passphrase")


def test_without_the_setting_the_install_credential_is_still_needed(database) -> None:
    service = _service(database, launcher=False)
    issued = service.exchange_login_code(service.issue_login_code(), user_agent=None)
    assert issued is not None

    with pytest.raises(ReauthenticationRequired):
        service.change_password(issued.principal, current=None, new_password=PASSWORD)


def test_a_launcher_session_older_than_the_window_must_present_the_credential(database) -> None:
    service = _service(database, launcher=True)
    issued = service.exchange_login_code(service.issue_login_code(), user_agent=None)
    assert issued is not None
    _age_sessions(database, 3600)

    with pytest.raises(ReauthenticationRequired):
        service.change_password(issued.principal, current=None, new_password=PASSWORD)


def test_the_exception_does_not_let_a_fresh_session_sign_others_out(database) -> None:
    service = _service(database, launcher=True)
    issued = service.exchange_login_code(service.issue_login_code(), user_agent=None)
    assert issued is not None

    # Revoking other sessions still needs the owner's credential: S-10 covers only a first password.
    with pytest.raises(ReauthenticationRequired):
        service.revoke_sessions(issued.principal, handle=None, credential=None)
