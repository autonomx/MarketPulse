"""The environment the gateway, its job worker and the database tools run with (roadmap MP-3.1).

The desktop supervisor starts ``python -m marketpulse.serve`` with a launch config (ports and folders) and the
secrets it keeps in the OS credential store. This module turns those into the complete child environment:

- it keeps only the system variables Windows Python needs (``SystemRoot``, ``TEMP``, proxies, ...);
- it drops every inherited ``OMNIX_*`` and ``PYTHON*`` variable, so nothing from a developer's Omnix leaks in;
- it sets everything MarketPulse runs Omnix with: trading only, production defaults, invite-only accounts, the
  exact origin, both database roles, the persisted ports, writable resources and every secret path under the
  MarketPulse data folder (never ``%LOCALAPPDATA%\\Omnix``).
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

# System variables kept from the supervisor's environment; everything else is dropped.
SYSTEM_ALLOWLIST = (
    "SystemRoot", "SYSTEMROOT", "windir", "WINDIR", "SystemDrive", "SYSTEMDRIVE",
    "TEMP", "TMP", "USERPROFILE", "USERNAME", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "ProgramData",
    "COMPUTERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "PATHEXT", "COMSPEC", "ComSpec",
    "HOME", "LANG", "LC_ALL", "TZ",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
)
DROPPED_PREFIXES = ("OMNIX_", "PYTHON", "MARKETPULSE_")
# Secrets the supervisor passes in; read here, then removed from the child environment.
SECRET_VARIABLES = (
    "MARKETPULSE_DB_OWNER_PASSWORD",
    "MARKETPULSE_DB_APP_PASSWORD",
    "MARKETPULSE_NOTIFY_SECRET",
    "MARKETPULSE_GOOGLE_CLIENT_SECRET",
)
DB_OWNER = "marketpulse_owner"
DB_APP = "omnix_app"
DB_NAME = "marketpulse"


@dataclass(frozen=True)
class LaunchConfig:
    """What the supervisor decides once and persists: folders, ports and optional Google sign-in."""

    install_root: Path
    data_dir: Path
    http_port: int
    pg_port: int
    metrics_port: int
    notify_port: int
    postgres_bin: Path | None = None
    google_client_id: str | None = None

    @classmethod
    def load(cls, path: Path) -> LaunchConfig:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            install_root=Path(raw["install_root"]),
            data_dir=Path(raw["data_dir"]),
            http_port=int(raw["http_port"]),
            pg_port=int(raw["pg_port"]),
            metrics_port=int(raw["metrics_port"]),
            notify_port=int(raw["notify_port"]),
            postgres_bin=Path(raw["postgres_bin"]) if raw.get("postgres_bin") else None,
            google_client_id=(raw.get("google_client_id") or None),
        )

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"

    @property
    def web_dist(self) -> Path:
        return self.install_root / "web" / "dist"


def _database_url(user: str, password: str, port: int) -> str:
    return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@127.0.0.1:{port}/{DB_NAME}"


def child_environment(config: LaunchConfig, inherited: Mapping[str, str] | None = None) -> dict[str, str]:
    """The complete environment for the gateway, the job worker and the database tools."""
    source = dict(os.environ if inherited is None else inherited)
    missing = [name for name in ("MARKETPULSE_DB_OWNER_PASSWORD", "MARKETPULSE_DB_APP_PASSWORD",
                                 "MARKETPULSE_NOTIFY_SECRET") if not source.get(name)]
    if missing:
        raise ValueError(f"the supervisor must pass {', '.join(missing)}")
    env = {name: source[name] for name in SYSTEM_ALLOWLIST if source.get(name)}
    system_root = source.get("SystemRoot") or source.get("SYSTEMROOT") or source.get("windir") or ""
    path_parts = []
    if config.postgres_bin is not None:
        # persistence/cli.py finds pg_dump and pg_restore on PATH.
        path_parts.append(str(config.postgres_bin))
    if system_root:
        path_parts += [str(Path(system_root) / "System32"), system_root]
    path_parts += [part for part in source.get("PATH", source.get("Path", "")).split(os.pathsep) if part]
    env["PATH"] = os.pathsep.join(dict.fromkeys(path_parts))

    data = config.data_dir
    secrets = data / "secrets"
    owner_url = _database_url(DB_OWNER, source["MARKETPULSE_DB_OWNER_PASSWORD"], config.pg_port)
    app_url = _database_url(DB_APP, source["MARKETPULSE_DB_APP_PASSWORD"], config.pg_port)
    env.update({
        # Omnix, trading only, as a deployed single-machine install.
        "OMNIX_FEATURES": "trading",
        "OMNIX_ENV": "production",
        "OMNIX_AUTH_MODE": "local",
        "OMNIX_LOCAL_JOB_WORKER": "1",
        "OMNIX_AUTH_REGISTRATION": "invite",
        "OMNIX_AUTH_LAUNCHER_FIRST_PASSWORD": "1",
        "OMNIX_AUTH_DESKTOP_HANDOFF": "1",
        "OMNIX_PRODUCT_NAME": "MarketPulse",
        # The one origin the windows load from; nothing else may post.
        "OMNIX_ALLOWED_ORIGINS": config.origin,
        # Migrations, backup and restore as the owner; the runtime as omnix_app under row-level security.
        "OMNIX_MIGRATION_DATABASE_URL": owner_url,
        "OMNIX_DATABASE_URL": app_url,
        "OMNIX_JOB_WORKER_METRICS_PORT": str(config.metrics_port),
        # Writable data and every credential under the MarketPulse data folder.
        "OMNIX_RESOURCES_DIR": str(data / "resources"),
        "OMNIX_PROVIDER_SECRETS_PATH": str(secrets / "provider-api-keys.dpapi"),
        "OMNIX_SECRET_STORE": "dpapi",
        "OMNIX_SECRET_STORE_PATH": str(secrets / "omnix-secrets.dpapi"),
        # macOS: MarketPulse's own Keychain item for its file key, apart from Omnix's (S-8).
        "OMNIX_KEYCHAIN_SERVICE": "MarketPulse",
        "OMNIX_LOG_FORMAT": "json",
        # Bytecode caches go to the data folder: the install folder (a signed bundle on macOS) is never written.
        "PYTHONPYCACHEPREFIX": str(data / "pycache"),
        # Desktop notifications go to the supervisor's loopback listener (S-6).
        "OMNIX_TRADING_DESKTOP_NOTIFY_URL": f"http://127.0.0.1:{config.notify_port}/notify",
        "OMNIX_TRADING_DESKTOP_NOTIFY_SECRET": source["MARKETPULSE_NOTIFY_SECRET"],
        "MARKETPULSE_WEB_DIST": str(config.web_dist),
    })
    google_secret = source.get("MARKETPULSE_GOOGLE_CLIENT_SECRET")
    if config.google_client_id and google_secret:
        # All three or none (auth/settings.py); the redirect is the usual callback on the persisted port.
        env["OMNIX_GOOGLE_CLIENT_ID"] = config.google_client_id
        env["OMNIX_GOOGLE_CLIENT_SECRET"] = google_secret
        env["OMNIX_GOOGLE_REDIRECT_URI"] = f"{config.origin}/api/auth/google/callback"
    return env


def gateway_command(config: LaunchConfig, python: str, scripts_dir: Path) -> list[str]:
    """The gateway command line (roadmap §4.3)."""
    return [
        python, str(scripts_dir / "run_omnix_gateway.py"),
        "--managed-stdin", "--api-replicas", "0",
        "--host", "127.0.0.1", "--port", str(config.http_port),
        "--app", "marketpulse.desktop_app:app",
    ]


def apply_environment(env: Mapping[str, str]) -> None:
    """Replace this process's environment with ``env`` (children inherit it)."""
    for name in list(os.environ):
        if name not in env:
            del os.environ[name]
    os.environ.update(env)


def owner_database_url(config: LaunchConfig, inherited: Mapping[str, str] | None = None) -> str:
    source = os.environ if inherited is None else inherited
    return _database_url(DB_OWNER, source["MARKETPULSE_DB_OWNER_PASSWORD"], config.pg_port)


__all__ = [
    "DB_APP", "DB_NAME", "DB_OWNER", "DROPPED_PREFIXES", "LaunchConfig", "SECRET_VARIABLES", "SYSTEM_ALLOWLIST",
    "apply_environment", "child_environment", "gateway_command", "owner_database_url",
]
