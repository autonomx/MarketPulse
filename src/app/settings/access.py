"""Process-installed settings and secret access.

This module is intentionally small: browser/user settings are PostgreSQL-backed
through SettingsService; secrets use the OS-protected provider secret store.
There is no JSON-file fallback.
"""
from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any

from app.security.provider_secret_store import (
    load_provider_secrets,
    save_provider_secrets,
)

from app.config.defaults import DEFAULT_SETTINGS
from .api import settings_dict
from .service import SettingsService

_LOCK = RLock()
_SERVICE: SettingsService | None = None


def install_settings_service(service: SettingsService) -> None:
    global _SERVICE
    with _LOCK:
        if _SERVICE is not None and _SERVICE is not service:
            raise RuntimeError("settings service is already installed")
        _SERVICE = service


def install_database_settings_service(database: Any) -> SettingsService:
    """Install the PostgreSQL-backed settings in a process without the gateway's composition.

    The TTS and image services read the same settings (provider configuration,
    model directories) as the gateway. Idempotent: an installed service is kept.
    """
    from app.security.tenant_context import TenantProvider

    from .registry import core_setting_specs

    with _LOCK:
        if _SERVICE is not None:
            return _SERVICE
    service = SettingsService(database, TenantProvider().current, specs=core_setting_specs())
    install_settings_service(service)
    return service


def reset_settings_service_for_tests() -> None:
    global _SERVICE
    with _LOCK:
        _SERVICE = None


def current_settings_service() -> SettingsService:
    with _LOCK:
        service = _SERVICE
    if service is None:
        raise RuntimeError("settings service is not installed")
    return service


def load_settings(*, allow_defaults_without_service: bool = False) -> dict[str, Any]:
    with _LOCK:
        service = _SERVICE
    if service is None:
        if allow_defaults_without_service:
            return deepcopy(DEFAULT_SETTINGS)
        raise RuntimeError("settings service is not installed")
    values = settings_dict(service)
    merged = deepcopy(DEFAULT_SETTINGS)
    merged.update(values)
    return merged


def save_settings(settings: dict[str, Any]) -> None:
    service = current_settings_service()
    unknown = set(settings).difference(service.specs)
    if unknown:
        raise KeyError(f"unknown setting key: {sorted(unknown)[0]}")
    for key, value in settings.items():
        current = service.get(key)
        expected = 0 if current is None else int(current["revision"])
        service.set(key, value, expected_revision=expected)


def load_secrets() -> dict[str, Any]:
    return load_provider_secrets()


def save_secrets(payload: dict[str, Any]) -> None:
    save_provider_secrets(payload)
