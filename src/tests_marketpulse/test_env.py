"""The child environment the gateway runs with (roadmap MP-3.1)."""
from __future__ import annotations

from pathlib import Path

import pytest

from marketpulse.env import LaunchConfig, child_environment, gateway_command

SECRETS = {
    "MARKETPULSE_DB_OWNER_PASSWORD": "owner pw/with:specials",
    "MARKETPULSE_DB_APP_PASSWORD": "app-pw",
    "MARKETPULSE_NOTIFY_SECRET": "notify-secret",
}


@pytest.fixture
def config(tmp_path) -> LaunchConfig:
    return LaunchConfig(install_root=tmp_path / "install", data_dir=tmp_path / "data", http_port=47800,
                        pg_port=47803, metrics_port=47801, notify_port=47802, postgres_bin=tmp_path / "pg" / "bin")


def _inherited(**extra: str) -> dict[str, str]:
    return {
        "SystemRoot": r"C:\Windows", "TEMP": r"C:\Temp", "USERPROFILE": r"C:\Users\me", "LOCALAPPDATA": r"C:\Users\me\AppData\Local",
        "PATH": r"C:\Tools;C:\Windows\System32", "HTTPS_PROXY": "http://proxy:8080",
        "OMNIX_DATABASE_URL": "postgresql://omnix:omnix@127.0.0.1:5432/omnix", "OMNIX_FEATURES": "all",
        "PYTHONPATH": r"C:\elsewhere", "PYTHONHOME": r"C:\python", "UNRELATED": "value",
        **SECRETS, **extra,
    }


def test_keeps_only_the_system_allowlist_and_drops_inherited_omnix_and_python(config):
    env = child_environment(config, _inherited())

    assert env["SystemRoot"] == r"C:\Windows" and env["TEMP"] == r"C:\Temp" and env["HTTPS_PROXY"] == "http://proxy:8080"
    assert "UNRELATED" not in env
    assert "PYTHONPATH" not in env and "PYTHONHOME" not in env
    assert not any(name.startswith("MARKETPULSE_DB") or name == "MARKETPULSE_NOTIFY_SECRET" for name in env)
    assert env["OMNIX_FEATURES"] == "trading"
    assert "5432" not in env["OMNIX_DATABASE_URL"]
    parts = env["PATH"].split(";") if ";" in env["PATH"] else env["PATH"].split(":")
    assert parts[0] == str(config.postgres_bin)


def test_sets_the_whole_marketpulse_environment(config):
    env = child_environment(config, _inherited())

    assert {key: env[key] for key in (
        "OMNIX_FEATURES", "OMNIX_ENV", "OMNIX_AUTH_MODE", "OMNIX_LOCAL_JOB_WORKER", "OMNIX_AUTH_REGISTRATION",
        "OMNIX_AUTH_LAUNCHER_FIRST_PASSWORD", "OMNIX_AUTH_DESKTOP_HANDOFF", "OMNIX_PRODUCT_NAME",
        "OMNIX_ALLOWED_ORIGINS", "OMNIX_JOB_WORKER_METRICS_PORT", "OMNIX_SECRET_STORE",
        "OMNIX_TRADING_DESKTOP_NOTIFY_URL", "OMNIX_TRADING_DESKTOP_NOTIFY_SECRET",
    )} == {
        "OMNIX_FEATURES": "trading", "OMNIX_ENV": "production", "OMNIX_AUTH_MODE": "local",
        "OMNIX_LOCAL_JOB_WORKER": "1", "OMNIX_AUTH_REGISTRATION": "invite",
        "OMNIX_AUTH_LAUNCHER_FIRST_PASSWORD": "1", "OMNIX_AUTH_DESKTOP_HANDOFF": "1", "OMNIX_PRODUCT_NAME": "MarketPulse",
        "OMNIX_ALLOWED_ORIGINS": "http://127.0.0.1:47800", "OMNIX_JOB_WORKER_METRICS_PORT": "47801",
        "OMNIX_SECRET_STORE": "dpapi", "OMNIX_TRADING_DESKTOP_NOTIFY_URL": "http://127.0.0.1:47802/notify",
        "OMNIX_TRADING_DESKTOP_NOTIFY_SECRET": "notify-secret",
    }
    # Migrations as the owner, the runtime as omnix_app; passwords are URL-encoded.
    assert env["OMNIX_MIGRATION_DATABASE_URL"] == (
        "postgresql://marketpulse_owner:owner%20pw%2Fwith%3Aspecials@127.0.0.1:47803/marketpulse")
    assert env["OMNIX_DATABASE_URL"] == "postgresql://omnix_app:app-pw@127.0.0.1:47803/marketpulse"


def test_every_writable_and_secret_path_is_under_the_marketpulse_data_folder(config):
    env = child_environment(config, _inherited())

    for key in ("OMNIX_RESOURCES_DIR", "OMNIX_PROVIDER_SECRETS_PATH", "OMNIX_SECRET_STORE_PATH", "PYTHONPYCACHEPREFIX"):
        assert Path(env[key]).is_relative_to(config.data_dir), key
    assert not any("\\Omnix\\" in value or "/Omnix/" in value for value in env.values())


def test_google_is_all_or_nothing(config):
    assert "OMNIX_GOOGLE_CLIENT_ID" not in child_environment(config, _inherited())
    with_google = LaunchConfig(**{**config.__dict__, "google_client_id": "client.apps.googleusercontent.com"})
    assert "OMNIX_GOOGLE_CLIENT_ID" not in child_environment(with_google, _inherited())

    env = child_environment(with_google, _inherited(MARKETPULSE_GOOGLE_CLIENT_SECRET="desktop-secret"))
    assert env["OMNIX_GOOGLE_CLIENT_ID"] == "client.apps.googleusercontent.com"
    assert env["OMNIX_GOOGLE_CLIENT_SECRET"] == "desktop-secret"
    assert env["OMNIX_GOOGLE_REDIRECT_URI"] == "http://127.0.0.1:47800/api/auth/google/callback"


def test_the_supervisor_must_pass_the_secrets(config):
    inherited = _inherited()
    del inherited["MARKETPULSE_NOTIFY_SECRET"]

    with pytest.raises(ValueError, match="MARKETPULSE_NOTIFY_SECRET"):
        child_environment(config, inherited)


def test_the_gateway_environment_has_no_owner_credentials(config):
    inherited = _inherited()
    gateway = child_environment(config, inherited, owner=False)
    del inherited["MARKETPULSE_DB_OWNER_PASSWORD"]

    # The running gateway reaches PostgreSQL only as omnix_app; the owner role is the cluster's superuser.
    assert "OMNIX_MIGRATION_DATABASE_URL" not in gateway
    assert not any("marketpulse_owner" in value or "owner pw" in value for value in gateway.values())
    assert child_environment(config, inherited, owner=False)["OMNIX_DATABASE_URL"].startswith("postgresql://omnix_app:")
    with pytest.raises(ValueError, match="MARKETPULSE_DB_OWNER_PASSWORD"):
        child_environment(config, inherited)


def test_gateway_command_line(config):
    command = gateway_command(config, "python.exe", Path("scripts"))

    assert command == [
        "python.exe", str(Path("scripts") / "run_omnix_gateway.py"), "--managed-stdin", "--api-replicas", "0",
        "--host", "127.0.0.1", "--port", "47800", "--app", "marketpulse.desktop_app:app",
    ]
