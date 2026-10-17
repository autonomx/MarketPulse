"""Settings Control Center over typed per-key SettingsService persistence."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.settings.access import (
    current_settings_service,
    load_secrets,
    load_settings,
    save_settings,
    save_secrets,
)
from app.settings.service import SettingRevisionConflict, SettingsPatch
from app.providers.catalog import API_KEY, providers_with
from app.providers.service import invalidate_provider_cache

from app.platform.voice.audio_cache import invalidate_changed_audio_caches
from app.composition.gateway.legacy_settings_payload import SettingsPayload, SettingsSaveResponse, apply_settings_payload
from app.composition.gateway.legacy_settings_payload import get_settings_payload as get_legacy_settings_payload
from app.settings.profile_core import SETTINGS_PROFILE_KEY
from app.settings.profile_repository import (
    SettingsProfileRevisionConflict,
    SettingsProfileValidationError,
    load_settings_profile,
    profile_payload,
    save_settings_profile,
)

PROFILE_PATCH_KEY = "settings_profile_patch"
PROFILE_BASE_REVISION_KEY = "base_revision"

# Runtime hooks consume this service-backed compatibility export until the
# settings UI and provider routing are composed directly through SettingsService.
__all__ = ["save_settings"]


def get_settings_payload() -> SettingsPayload:
    payload = get_legacy_settings_payload()
    settings = load_settings()
    profile = load_settings_profile(settings)
    serialized_profile = profile_payload(profile)
    provider_configs = serialized_profile.get("providerConfigs", {})
    for provider_id in providers_with(API_KEY):
        displayed = payload.settings.get(provider_id, {})
        profile_config = provider_configs.get(provider_id, {})
        if isinstance(displayed, dict) and isinstance(profile_config, dict):
            profile_config["apiKey"] = str(displayed.get("api_key") or "")
    payload.settings[SETTINGS_PROFILE_KEY] = serialized_profile
    return payload


def _legacy_request(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in data.items()
        if key not in {PROFILE_PATCH_KEY, PROFILE_BASE_REVISION_KEY}
    }


def _provider_patch(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "global": {
            "providers": {
                "llm": str(settings.get("provider") or "lmstudio"),
                "tts": str(settings.get("audio_provider_tts") or "faster-qwen3-tts"),
                "stt": str(settings.get("audio_provider_stt") or "parakeet"),
            }
        }
    }


def _codex_profile_changed(patch: Any) -> bool:
    if not isinstance(patch, dict):
        return False
    configs = patch.get("providerConfigs")
    return isinstance(configs, dict) and bool({"chatgptCodex", "claudeCli"} & set(configs))


def _changed_values(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in after.items()
        if before.get(key) != value
    }


def save_settings_payload(data: dict[str, Any]) -> SettingsSaveResponse:
    service = current_settings_service()
    settings = load_settings()
    previous_settings = deepcopy(settings)
    secrets = load_secrets()
    legacy = _legacy_request(data)
    patch = data.get(PROFILE_PATCH_KEY)
    base_revision = data.get(PROFILE_BASE_REVISION_KEY)
    codex_profile_changed = _codex_profile_changed(patch)

    try:
        if isinstance(patch, dict) and base_revision:
            current = load_settings_profile(settings)
            if current.revision != str(base_revision):
                raise SettingsProfileRevisionConflict(
                    str(base_revision),
                    current.revision,
                )

        before_keys = dict(secrets.get("api_keys") or {})
        secrets_changed = apply_settings_payload(settings, secrets, legacy) if legacy else False
        if secrets_changed:
            # Only the keys this request changed, before anything else is saved: a
            # secret-store failure then leaves the settings untouched, and a store
            # that read as empty cannot erase the providers the user did not edit.
            after_keys = dict(secrets.get("api_keys") or {})
            changed_keys = {
                provider: str(after_keys.get(provider) or "")
                for provider in set(before_keys) | set(after_keys)
                if before_keys.get(provider) != after_keys.get(provider)
            }
            save_secrets({"api_keys": changed_keys})
        if isinstance(patch, dict):
            save_settings_profile(settings, patch, None)
        elif legacy:
            current = load_settings_profile(settings)
            save_settings_profile(settings, _provider_patch(settings), current.revision)
        else:
            load_settings_profile(settings)

        changed = _changed_values(previous_settings, settings)
        revisions: dict[str, int] = {}
        for key in changed:
            stored = service.get(key)
            revisions[key] = 0 if stored is None else int(stored["revision"])

        if changed:
            service.patch(SettingsPatch(values=changed, revisions=revisions))

        provider_settings_changed = bool(
            set(changed).intersection(
                {"provider", "lmstudio", "openrouter", "cerebras"}
            )
        )
        if codex_profile_changed or provider_settings_changed or secrets_changed:
            invalidate_provider_cache()
        invalidate_changed_audio_caches(previous_settings, settings)
    except (
        KeyError,
        PermissionError,
        SettingRevisionConflict,
        SettingsProfileRevisionConflict,
        SettingsProfileValidationError,
        TypeError,
    ):
        return SettingsSaveResponse(success=False)

    return SettingsSaveResponse(success=True)
