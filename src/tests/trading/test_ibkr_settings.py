from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.market_data_api import create_trading_market_data_router
from app.apps.trading.ibkr_settings import load_ibkr_settings, save_ibkr_settings
from app.apps.trading.providers.ibkr_runtime import IbkrRuntime


def _install_settings_double(monkeypatch):
    from app.settings import access

    state: dict = {}

    def load_settings(*, allow_defaults_without_service=False):
        del allow_defaults_without_service
        return deepcopy(state)

    def save_settings(payload):
        state.clear()
        state.update(deepcopy(payload))

    monkeypatch.setattr(access, "load_settings", load_settings)
    monkeypatch.setattr(access, "save_settings", save_settings)
    return state


def _clear_ibkr_environment(monkeypatch) -> None:
    for name in (
        "OMNIX_IBKR_ENABLED",
        "OMNIX_IBKR_MONITOR",
        "OMNIX_IBKR_HOST",
        "OMNIX_IBKR_PORT",
        "OMNIX_IBKR_CLIENT_ID",
        "OMNIX_IBKR_LIVE_AUTHORITY",
        "OMNIX_IBKR_RECOVERY_AUTHORITY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_saved_ibkr_settings_are_durable_and_drive_default_runtime(monkeypatch) -> None:
    state = _install_settings_double(monkeypatch)
    _clear_ibkr_environment(monkeypatch)

    saved = save_ibkr_settings(
        {
            "enabled": True,
            "monitor_enabled": False,
            "host": "192.168.1.50",
            "port": 4001,
            "client_id": 93,
        }
    )

    assert saved.enabled is True
    assert saved.host == "192.168.1.50"
    assert load_ibkr_settings()[1] == "omnix_settings"
    assert state["trading_market_data"]["ibkr"] == {
        "enabled": True,
        "monitor_enabled": False,
        "host": "192.168.1.50",
        "port": 4001,
        "client_id": 93,
        "live_authority_enabled": False,
        "recovery_authority_enabled": False,
    }

    runtime = IbkrRuntime()
    diagnostics = runtime.diagnostics()
    assert diagnostics["settings_source"] == "omnix_settings"
    assert diagnostics["enabled"] is True
    assert diagnostics["monitor_enabled"] is False
    assert diagnostics["host"] == "192.168.1.50"
    assert diagnostics["port"] == 4001
    assert diagnostics["client_id"] == 93
    assert diagnostics["live_authority_enabled"] is False
    assert diagnostics["recovery_authority_enabled"] is False


def test_saved_settings_override_legacy_environment_configuration(monkeypatch) -> None:
    _install_settings_double(monkeypatch)
    monkeypatch.setenv("OMNIX_IBKR_ENABLED", "1")
    monkeypatch.setenv("OMNIX_IBKR_HOST", "environment-host")
    save_ibkr_settings({"enabled": False, "host": "omnix-host"})

    settings, source = load_ibkr_settings()

    assert source == "omnix_settings"
    assert settings.enabled is False
    assert settings.host == "omnix-host"


def test_ibkr_settings_endpoint_returns_status_and_persists_update(monkeypatch) -> None:
    state = _install_settings_double(monkeypatch)
    _clear_ibkr_environment(monkeypatch)

    app = FastAPI()
    app.include_router(create_trading_market_data_router())
    client = TestClient(app)

    initial = client.get("/api/trading/market-data/providers/ibkr/settings")
    assert initial.status_code == 200
    assert initial.json()["connection_status"] == "disabled"
    assert isinstance(initial.json()["official_ibapi_available"], bool)

    updated = client.put(
        "/api/trading/market-data/providers/ibkr/settings",
        json={"enabled": True, "port": 4001, "client_id": 94},
    )

    assert updated.status_code == 200
    payload = updated.json()
    assert payload["settings_source"] == "omnix_settings"
    assert payload["settings"]["enabled"] is True
    assert payload["settings"]["port"] == 4001
    assert payload["settings"]["client_id"] == 94
    assert state["trading_market_data"]["ibkr"]["client_id"] == 94
    expected_status = (
        "disconnected"
        if payload["official_ibapi_available"]
        else "client_unavailable"
    )
    assert payload["connection_status"] == expected_status


def test_ibkr_settings_save_through_the_real_settings_contract(monkeypatch) -> None:
    """The settings service rejects unregistered keys; the IBKR section must be registered."""

    from app.settings import access
    from tests.support.in_memory_settings import InMemorySettingsService

    _clear_ibkr_environment(monkeypatch)
    service = InMemorySettingsService()
    monkeypatch.setattr(access, "_SERVICE", service)

    saved = save_ibkr_settings({"enabled": True, "monitor_enabled": True, "port": 4002, "client_id": 71})

    assert saved.enabled is True
    assert service.values["trading_market_data"]["ibkr"]["client_id"] == 71
    settings, source = load_ibkr_settings()
    assert (settings.enabled, source) == (True, "omnix_settings")
