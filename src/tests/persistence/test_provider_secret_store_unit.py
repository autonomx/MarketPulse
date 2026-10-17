from __future__ import annotations

from app.security import provider_secret_store as store


def test_provider_keys_are_protected_and_environment_values_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store.sys, "platform", "win32")
    path = tmp_path / "provider-keys.dpapi"
    monkeypatch.setenv("OMNIX_PROVIDER_SECRETS_PATH", str(path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.setattr(store, "_protect", lambda value: b"protected:" + value[::-1])
    monkeypatch.setattr(store, "_unprotect", lambda value: value.removeprefix(b"protected:")[::-1])

    store.save_provider_secrets(
        {"api_keys": {"openrouter": "or-secret", "cerebras": "cerebras-secret"}}
    )

    assert b"or-secret" not in path.read_bytes()
    assert b"cerebras-secret" not in path.read_bytes()
    assert store.load_provider_secrets() == {
        "api_keys": {"openrouter": "or-secret", "cerebras": "cerebras-secret"}
    }

    monkeypatch.setenv("CEREBRAS_API_KEY", "environment-secret")
    store.save_provider_secrets(
        {"api_keys": {"openrouter": "or-updated", "cerebras": "ignored-ui-value"}}
    )
    assert store.load_provider_secrets()["api_keys"] == {
        "openrouter": "or-updated",
        "cerebras": "environment-secret",
    }

    monkeypatch.delenv("CEREBRAS_API_KEY")
    assert store.load_provider_secrets()["api_keys"]["cerebras"] == "cerebras-secret"


def test_empty_provider_key_deletes_the_protected_value(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store.sys, "platform", "win32")
    monkeypatch.setenv("OMNIX_PROVIDER_SECRETS_PATH", str(tmp_path / "provider-keys.dpapi"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.setattr(store, "_protect", lambda value: value[::-1])
    monkeypatch.setattr(store, "_unprotect", lambda value: value[::-1])

    store.save_provider_secrets({"api_keys": {"cerebras": "secret"}})
    store.save_provider_secrets({"api_keys": {"cerebras": ""}})

    assert store.load_provider_secrets()["api_keys"]["cerebras"] == ""


def test_alpaca_credentials_are_dpapi_protected_and_preserve_provider_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store.sys, "platform", "win32")
    path = tmp_path / "provider-keys.dpapi"
    monkeypatch.setenv("OMNIX_PROVIDER_SECRETS_PATH", str(path))
    for name in (
        "OPENROUTER_API_KEY",
        "CEREBRAS_API_KEY",
        "OMNIX_ALPACA_API_KEY_ID",
        "APCA_API_KEY_ID",
        "OMNIX_ALPACA_API_SECRET_KEY",
        "APCA_API_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(store, "_protect", lambda value: b"protected:" + value[::-1])
    monkeypatch.setattr(store, "_unprotect", lambda value: value.removeprefix(b"protected:")[::-1])

    store.save_provider_secrets({"api_keys": {"openrouter": "or-secret"}})
    store.save_trading_provider_secrets(
        "alpaca_iex",
        {"api_key_id": "alpaca-key", "secret_key": "alpaca-secret"},
    )

    raw = path.read_bytes()
    assert b"alpaca-key" not in raw
    assert b"alpaca-secret" not in raw
    assert store.load_provider_secrets()["api_keys"]["openrouter"] == "or-secret"
    assert store.load_trading_provider_secrets()["alpaca_iex"] == {
        "api_key_id": "alpaca-key",
        "secret_key": "alpaca-secret",
    }
    assert store.trading_provider_credential_sources("alpaca_iex") == {
        "api_key_id": "os_protected_store",
        "secret_key": "os_protected_store",
    }


def test_alpaca_environment_credentials_are_authoritative_and_not_overwritten(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store.sys, "platform", "win32")
    monkeypatch.setenv("OMNIX_PROVIDER_SECRETS_PATH", str(tmp_path / "provider-keys.dpapi"))
    monkeypatch.setattr(store, "_protect", lambda value: value[::-1])
    monkeypatch.setattr(store, "_unprotect", lambda value: value[::-1])
    monkeypatch.setenv("OMNIX_ALPACA_API_KEY_ID", "environment-key")
    monkeypatch.setenv("OMNIX_ALPACA_API_SECRET_KEY", "environment-secret")

    store.save_trading_provider_secrets(
        "alpaca_iex",
        {"api_key_id": "ignored-key", "secret_key": "ignored-secret"},
    )

    assert store.load_trading_provider_secrets()["alpaca_iex"] == {
        "api_key_id": "environment-key",
        "secret_key": "environment-secret",
    }
    assert store.trading_provider_credential_sources("alpaca_iex") == {
        "api_key_id": "environment",
        "secret_key": "environment",
    }


def test_a_stored_or_environment_fred_key_is_read_back(tmp_path, monkeypatch) -> None:
    """The economic calendar's FRED key reaches load_trading_provider_secrets (it was dropped before)."""
    monkeypatch.setattr(store.sys, "platform", "win32")
    monkeypatch.setenv("OMNIX_PROVIDER_SECRETS_PATH", str(tmp_path / "provider-keys.dpapi"))
    monkeypatch.delenv("OMNIX_FRED_API_KEY", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(store, "_protect", lambda value: value[::-1])
    monkeypatch.setattr(store, "_unprotect", lambda value: value[::-1])

    store.save_trading_provider_secrets("fred", {"api_key": "fred-stored"})
    assert store.load_trading_provider_secrets()["fred"] == {"api_key": "fred-stored"}
    assert store.trading_provider_credential_sources("fred") == {"api_key": "os_protected_store"}

    monkeypatch.setenv("FRED_API_KEY", "fred-environment")
    assert store.load_trading_provider_secrets()["fred"] == {"api_key": "fred-environment"}
