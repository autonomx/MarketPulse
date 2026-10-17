"""Omnix boots with only the trading feature (MarketPulse S-1).

MarketPulse runs Omnix's production gateway with ``OMNIX_FEATURES=trading``.
This test keeps that way of running Omnix working from the authority side: the
real production application starts through its lifespan against PostgreSQL,
answers health, readiness, the auth session and a trading route, and serves no
other feature's routes.

MarketPulse ships without other features' code (roadmap Phase 12), so a
trading-only boot must not import it: only each feature's declarations, which
every module provides, are read.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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
        # RPG's replay routes belong to RPG: without it they are not mounted.
        assert client.get("/api/replay/primitives").status_code == 404
        gateway = application._application
        enabled = set(gateway.state.runtime_config.enabled_features)
        assert enabled == {"trading"}


# Packages MarketPulse leaves out, and the only modules of theirs a trading-only boot may load.
OTHER_FEATURES = (
    "app.apps.rpg", "app.apps.story", "app.apps.audiobook", "app.apps.desktop_companion",
    "app.apps.character_interactions", "app.platform.image", "app.platform.research", "app.platform.characters",
    "app.platform.live_speech", "app.platform.companion_activity", "app.providers.vendor",
)
DECLARATIONS = {"app.apps.rpg", "app.apps.rpg.declarations", "app.apps.story", "app.apps.story.declarations"}
_BOOT = """
import os, sys
from fastapi.testclient import TestClient
from app.composition.production import ProductionApplication
with TestClient(ProductionApplication(), base_url="http://127.0.0.1", headers={"X-Omnix-Client": "test"}) as client:
    assert client.get("/ready").status_code == 200
    assert client.get("/api/trading/providers").status_code == 200
print("\\n".join(sorted(name for name in sys.modules if name.startswith("app."))))
sys.stdout.flush()
os._exit(0)
"""


def test_a_trading_only_boot_imports_no_other_features_code(trading_only_environment):
    # A fresh process: this one has imported whatever earlier tests needed.
    source_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(source_root), os.environ.get("PYTHONPATH", "")])}
    result = subprocess.run([sys.executable, "-c", _BOOT], capture_output=True, text=True, env=env, timeout=300)
    assert result.returncode == 0, result.stderr[-3000:]
    loaded = set(result.stdout.split())

    others = {name for name in loaded if name.startswith(OTHER_FEATURES)}

    assert others <= DECLARATIONS, sorted(others - DECLARATIONS)
