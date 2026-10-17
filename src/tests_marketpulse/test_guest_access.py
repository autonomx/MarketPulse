"""Phase 7 gate: a guest cannot reach owner-only settings in the trading-only gateway, research handoff token included."""
from __future__ import annotations

import dataclasses
import os
import secrets
import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not os.environ.get("OMNIX_TEST_DATABASE_URL"), reason="OMNIX_TEST_DATABASE_URL is not set"),
]

PASSWORD = "correct horse battery staple"
FRED_KEY = "fred-key-0123456789abcdef"
# Changing credentials or broker settings, and reading the research handoff token, need trading:control.
OWNER_ONLY = [
    ("GET", "/api/trading/prospective-gap/github-credentials", None),
    ("PUT", "/api/trading/prospective-gap/github-credentials", {"token": "ghp_guess"}),
    ("PUT", "/api/trading/market-data/providers/fred/credentials", {"api_key": "guess"}),
    ("PUT", "/api/trading/execution/providers/alpaca-iex/credentials", {"api_key": "guess", "secret_key": "guess"}),
    ("PUT", "/api/trading/market-data/providers/ibkr/settings", {"host": "127.0.0.1", "port": 7497}),
]
# Omnix lets members (guests included) read a provider's status: configured or not, and a masked key.
STATUS_READS = [
    "/api/trading/market-data/providers/fred/credentials",
    "/api/trading/execution/providers/alpaca-iex/credentials",
    "/api/trading/market-data/providers/ibkr/settings",
]


@pytest.fixture
def gateway(monkeypatch, tmp_path):
    from app.composition.gateway.main import create_gateway_app
    from app.persistence.config import DatabaseSettings
    from app.persistence.database import PostgresDatabase
    from app.persistence.identity_service import ensure_local_identity
    from app.security.auth import AuthService, resolve_auth_settings

    monkeypatch.setenv("OMNIX_FEATURES", "trading")
    # Credentials the owner saves stay in this test's folder.
    monkeypatch.setenv("OMNIX_PROVIDER_SECRETS_PATH", str(tmp_path / "secrets" / "provider-api-keys.dpapi"))
    for name in ("OMNIX_FRED_API_KEY", "FRED_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    database = PostgresDatabase(DatabaseSettings(url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=3,
                                                 application_name="marketpulse-guest-tests"))
    ensure_local_identity(database)
    settings = resolve_auth_settings({"OMNIX_AUTH_MODE": "local"})
    service = AuthService(dataclasses.replace(settings, accounts=dataclasses.replace(settings.accounts, guests=True)),
                          database=database)
    service.sync_install_credential(secrets.token_urlsafe(32))
    try:
        yield create_gateway_app(auth_service=service)
    finally:
        database.close()


def _client(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1", headers={"X-Omnix-Client": "test"}, follow_redirects=False)


def _call(client: TestClient, method: str, path: str, body=None):
    return client.request(method, path, json=body, headers={"X-Omnix-CSRF": client.cookies.get("omnix_csrf") or ""})


def test_a_guest_cannot_change_credentials_or_read_the_research_token(gateway):
    owner = _client(gateway)
    registered = owner.post("/api/auth/register", json={"email": f"owner-{uuid.uuid4().hex[:10]}@example.com",
                                                        "password": PASSWORD, "display_name": "Owner"})
    assert registered.status_code in (200, 201), registered.text
    saved = _call(owner, "PUT", "/api/trading/market-data/providers/fred/credentials", {"api_key": FRED_KEY})
    assert saved.status_code == 200, saved.text
    assert _call(owner, "GET", "/api/trading/prospective-gap/github-credentials").status_code == 200

    guest = _client(gateway)
    assert guest.post("/api/auth/guest").status_code == 201
    refused = {(method, path): _call(guest, method, path, body).status_code for method, path, body in OWNER_ONLY}
    statuses = {path: _call(guest, "GET", path) for path in STATUS_READS}

    assert set(refused.values()) == {403}, refused
    assert {path: response.status_code for path, response in statuses.items()} == dict.fromkeys(STATUS_READS, 200)
    fred = statuses["/api/trading/market-data/providers/fred/credentials"].json()
    assert fred["configured"] is True and FRED_KEY not in str(fred) and FRED_KEY[:-4] not in str(fred)
