from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading import execution_api


def test_alpaca_credential_status_is_masked_and_never_returns_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        execution_api,
        "load_trading_provider_secrets",
        lambda: {"alpaca_iex": {"api_key_id": "PK12345678", "secret_key": "super-secret"}},
    )
    monkeypatch.setattr(
        execution_api,
        "trading_provider_credential_sources",
        lambda provider: {"api_key_id": "os_protected_store", "secret_key": "os_protected_store"},
    )
    monkeypatch.setattr(execution_api.sys, "platform", "win32")
    app = FastAPI()
    app.include_router(execution_api.create_trading_execution_router())

    response = TestClient(app).get("/api/trading/execution/providers/alpaca-iex/credentials")

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["api_key_id_masked"] == "***5678"
    assert payload["api_key_source"] == "os_protected_store"
    assert payload["secret_key_source"] == "os_protected_store"
    assert payload["api_key_editable"] is True
    assert payload["secret_key_editable"] is True
    assert "super-secret" not in response.text
    assert "secret_key" not in payload


def test_alpaca_credential_update_passes_only_explicit_fields_to_protected_store(monkeypatch) -> None:
    state = {"api_key_id": "old-key", "secret_key": "old-secret"}

    def load():
        return {"alpaca_iex": dict(state)}

    def save(provider, updates):
        assert provider == "alpaca_iex"
        for key, value in updates.items():
            if value:
                state[key] = value
            else:
                state.pop(key, None)

    monkeypatch.setattr(execution_api, "load_trading_provider_secrets", load)
    monkeypatch.setattr(execution_api, "save_trading_provider_secrets", save)
    monkeypatch.setattr(
        execution_api,
        "trading_provider_credential_sources",
        lambda provider: {
            "api_key_id": "os_protected_store" if state.get("api_key_id") else "missing",
            "secret_key": "os_protected_store" if state.get("secret_key") else "missing",
        },
    )
    monkeypatch.setattr(execution_api.sys, "platform", "win32")
    app = FastAPI()
    app.include_router(execution_api.create_trading_execution_router())
    client = TestClient(app)

    response = client.put(
        "/api/trading/execution/providers/alpaca-iex/credentials",
        json={"secret_key": "new-secret"},
    )

    assert response.status_code == 200
    assert state == {"api_key_id": "old-key", "secret_key": "new-secret"}
    assert response.json()["configured"] is True
    assert "new-secret" not in response.text
