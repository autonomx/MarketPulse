"""Secret storage (WP-4.9).

Secrets (assistant-tool OAuth tokens and clients, and anything else that
must not live in PostgreSQL or the settings document) go through a
``SecretStore``. There is no plaintext file backend.

Backends, chosen by ``OMNIX_SECRET_STORE`` (``auto``, ``env``, ``dpapi``,
``keyring``):

- ``dpapi``: Windows DPAPI, user-scoped encryption, one protected file
  (``OMNIX_SECRET_STORE_PATH``, default under ``%LOCALAPPDATA%/Omnix``);
- ``keyring``: the OS keychain through the optional ``keyring`` package
  (macOS Keychain, Secret Service on Linux);
- ``env``: read-only, ``OMNIX_SECRET_<NAME>`` environment variables;
- ``auto``: DPAPI on Windows, else keyring when installed, else env.

External vaults (HashiCorp Vault, Azure Key Vault) implement the same
``SecretStore`` protocol; see ``ExternalVaultSecretStore``.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Protocol

from app.config.env import env_str, environment

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
_STORE_LOCK = threading.Lock()
_STORE: "SecretStore | None" = None


class SecretStoreUnavailable(RuntimeError):
    """No writable secret store is available in this environment."""


class SecretStore(Protocol):
    name: str
    writable: bool

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...

    def names(self) -> list[str]: ...


class ExternalVaultSecretStore(Protocol):
    """Implement ``SecretStore`` against an external vault.

    Map ``name`` to a vault path (for example ``secret/omnix/<name>`` in
    HashiCorp Vault KV v2, or a Key Vault secret named with ``/`` replaced by
    ``--``), authenticate with the platform's workload identity, and install
    the store with ``install_secret_store``. Omnix does not ship one.
    """


def _check_name(name: str) -> str:
    if not _NAME.fullmatch(str(name)):
        raise ValueError("secret names use letters, digits and _ . : / - (at most 200 characters)")
    return str(name)


class EnvSecretStore:
    """Read-only: ``OMNIX_SECRET_<NAME>`` with non-alphanumerics as ``_``."""

    name = "env"
    writable = False

    @staticmethod
    def variable(name: str) -> str:
        return "OMNIX_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", _check_name(name)).upper()

    def get(self, name: str) -> str | None:
        return environment().get(self.variable(name)) or None

    def set(self, name: str, value: str) -> None:
        raise SecretStoreUnavailable("the environment secret store is read-only")

    def delete(self, name: str) -> None:
        raise SecretStoreUnavailable("the environment secret store is read-only")

    def names(self) -> list[str]:
        return []


class DpapiSecretStore:
    """Windows DPAPI: one user-scoped encrypted file holding all secrets."""

    name = "dpapi"
    writable = True
    path: Path
    _lock: threading.Lock

    def __init__(self, path: Path | None = None) -> None:
        if sys.platform != "win32":
            raise SecretStoreUnavailable("DPAPI is only available on Windows")
        self.path = path or default_dpapi_path()
        self._lock = threading.Lock()

    def _read(self) -> dict[str, str]:
        from app.security.provider_secret_store import _unprotect

        if not self.path.exists():
            return {}
        payload = json.loads(_unprotect(self.path.read_bytes()).decode("utf-8"))
        return {str(key): str(value) for key, value in dict(payload).items()}

    def _write(self, secrets: dict[str, str]) -> None:
        from app.security.provider_secret_store import _protect

        self.path.parent.mkdir(parents=True, exist_ok=True)
        protected = _protect(json.dumps(secrets, sort_keys=True).encode("utf-8"))
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(protected)
        os.replace(temporary, self.path)

    def get(self, name: str) -> str | None:
        with self._lock:
            return self._read().get(_check_name(name))

    def set(self, name: str, value: str) -> None:
        with self._lock:
            secrets = self._read()
            secrets[_check_name(name)] = str(value)
            self._write(secrets)

    def delete(self, name: str) -> None:
        with self._lock:
            secrets = self._read()
            if secrets.pop(_check_name(name), None) is not None:
                self._write(secrets)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._read())


class KeyringSecretStore:
    """The OS keychain through ``keyring`` (optional dependency)."""

    name = "keyring"
    writable = True
    _SERVICE = "omnix"
    _INDEX = "__omnix_secret_names__"

    def __init__(self) -> None:
        try:
            import keyring
        except ImportError as exc:
            raise SecretStoreUnavailable("install the keyring package to use the OS keychain") from exc
        self._keyring = keyring
        self._lock = threading.Lock()

    def _index(self) -> list[str]:
        raw = self._keyring.get_password(self._SERVICE, self._INDEX)
        return sorted(json.loads(raw)) if raw else []

    def get(self, name: str) -> str | None:
        return self._keyring.get_password(self._SERVICE, _check_name(name))

    def set(self, name: str, value: str) -> None:
        with self._lock:
            self._keyring.set_password(self._SERVICE, _check_name(name), str(value))
            index = set(self._index()) | {name}
            self._keyring.set_password(self._SERVICE, self._INDEX, json.dumps(sorted(index)))

    def delete(self, name: str) -> None:
        with self._lock:
            try:
                self._keyring.delete_password(self._SERVICE, _check_name(name))
            except self._keyring.errors.PasswordDeleteError:
                pass
            index = set(self._index()) - {name}
            self._keyring.set_password(self._SERVICE, self._INDEX, json.dumps(sorted(index)))

    def names(self) -> list[str]:
        return self._index()


def default_dpapi_path() -> Path:
    configured = (env_str("OMNIX_SECRET_STORE_PATH", "") or "").strip()
    if configured:
        return Path(configured)
    local_app_data = environment().get("LOCALAPPDATA", "").strip() or str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "Omnix" / "secrets" / "omnix-secrets.dpapi"


def create_secret_store(kind: str | None = None) -> SecretStore:
    selected = (kind or env_str("OMNIX_SECRET_STORE", "auto") or "auto").strip().lower()
    if selected == "env":
        return EnvSecretStore()
    if selected == "dpapi":
        return DpapiSecretStore()
    if selected == "keyring":
        return KeyringSecretStore()
    if selected != "auto":
        raise ValueError("OMNIX_SECRET_STORE must be auto, env, dpapi or keyring")
    if sys.platform == "win32":
        return DpapiSecretStore()
    try:
        return KeyringSecretStore()
    except SecretStoreUnavailable:
        return EnvSecretStore()


def secret_store() -> SecretStore:
    """The process's secret store (created on first use)."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = create_secret_store()
        return _STORE


def install_secret_store(store: SecretStore | None) -> None:
    """Replace the process's store (tests, external vaults); ``None`` resets."""
    global _STORE
    with _STORE_LOCK:
        _STORE = store


def require_writable(store: SecretStore) -> SecretStore:
    if not store.writable:
        raise SecretStoreUnavailable(
            f"the {store.name} secret store is read-only; set OMNIX_SECRET_STORE to dpapi or keyring"
        )
    return store


__all__ = [
    "DpapiSecretStore",
    "EnvSecretStore",
    "ExternalVaultSecretStore",
    "KeyringSecretStore",
    "SecretStore",
    "SecretStoreUnavailable",
    "create_secret_store",
    "default_dpapi_path",
    "install_secret_store",
    "require_writable",
    "secret_store",
]
