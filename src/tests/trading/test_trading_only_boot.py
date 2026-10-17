"""Omnix boots with only the trading feature (MarketPulse S-1).

MarketPulse runs Omnix's production gateway with ``OMNIX_FEATURES=trading``.
This test keeps that way of running Omnix working from the authority side: the
real production application starts through its lifespan against PostgreSQL,
answers health, readiness, the auth session and a trading route, and serves no
other feature's routes.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.postgres


@pytest.fixture
def trading_only_environment(monkeypatch):
    if not os.environ.get("OMNIX_TEST_DATABASE_URL"):
        pytest.skip("OMNIX_TEST_DATABASE_URL is not set")
    monkeypatch.setenv("OMNIX_FEATURES", "trading")
    monkeypatch.setenv("OMNIX_DATABASE_URL", os.environ["OMNIX_TEST_DATABASE_URL"])


def test_production_gateway_boots_with_only_trading(trading_only_environment):
    from app.composition.production import ProductionApplication

    application = ProductionApplication()
    with TestClient(application, base_url="http://127.0.0.1", headers={"X-Omnix-Client": "test"}) as client:
        assert client.get("/health").status_code == 200
        ready = client.get("/ready")
        assert ready.status_code == 200, ready.text
        assert ready.json()["ready"] is True

        session = client.get("/api/auth/session")
        assert session.status_code == 200, session.text

        providers = client.get("/api/trading/providers")
        assert providers.status_code == 200, providers.text

        assert client.get("/api/audiobook/projects").status_code == 404
        gateway = application._application
        enabled = set(gateway.state.runtime_config.enabled_features)
        assert enabled == {"trading"}
