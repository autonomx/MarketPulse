"""Install an isolated, revision-aware SettingsService test runtime."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.composition.gateway import settings_control
from app.settings import access as settings_access
from app.settings.registry import CORE_SETTING_SPECS
from app.settings.service import SettingsPatch

from .in_memory_settings import InMemorySettingsService


def install_settings_test_runtime(
    monkeypatch,
    *,
    values: dict[str, Any] | None = None,
    secrets: dict[str, Any] | None = None,
) -> tuple[InMemorySettingsService, dict[str, Any]]:
    service = InMemorySettingsService(values)
    service.register_specs(CORE_SETTING_SPECS)
    secret_state = deepcopy(secrets or {"api_keys": {}})

    def load_secrets() -> dict[str, Any]:
        return deepcopy(secret_state)

    def save_secrets(payload: dict[str, Any]) -> None:
        # Like the protected store: a patch of the named providers; "" deletes one.
        api_keys = secret_state.setdefault("api_keys", {})
        for provider, value in dict(payload.get("api_keys") or {}).items():
            if value:
                api_keys[provider] = value
            else:
                api_keys.pop(provider, None)

    monkeypatch.setattr(settings_access, "_SERVICE", service)
    monkeypatch.setattr(settings_access, "load_secrets", load_secrets)
    monkeypatch.setattr(settings_access, "save_secrets", save_secrets)
    monkeypatch.setattr(settings_control, "load_secrets", load_secrets)
    monkeypatch.setattr(settings_control, "save_secrets", save_secrets)
    return service, secret_state


def apply_settings_patch(service: InMemorySettingsService, values: dict[str, Any]) -> None:
    revisions = {
        key: int((service.get(key) or {}).get("revision", 0))
        for key in values
    }
    service.patch(SettingsPatch(values=values, revisions=revisions))
