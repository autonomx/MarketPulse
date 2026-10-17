"""Sanitized settings summary for the web gateway."""
from __future__ import annotations

import json
from typing import Any

from app.settings.models import SettingsPayload, SettingsSaveResponse


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_copy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = _deep_copy(value)
    return merged


def _merge_settings_section(settings: dict[str, Any], key: str, incoming: Any, default: dict[str, Any] | None = None) -> None:
    existing = _safe_dict(settings.get(key))
    fallback = _deep_copy(default or {})
    settings[key] = _deep_merge(_deep_merge(fallback, existing), _safe_dict(incoming))


def _without_api_key(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "api_key"}


def _masked_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    return f"***{api_key[-4:]}" if len(api_key) > 4 else "****"


def _hermes_status_payload() -> dict[str, Any]:
    try:
        from app.providers.hermes_status import hermes_status_payload

        return hermes_status_payload()
    except Exception as exc:
        return {"enabled": False, "reachable": False, "base_url": "http://127.0.0.1:8642", "error": str(exc)}


def _hermes_commands_payload() -> dict[str, str]:
    return {
        "configure": "hermes setup && hermes model",
        "setup": "hermes setup",
        "model": "hermes model",
        "start_sidecar": "hermes serve",
        "enable_env": "HERMES_ENABLED=true",
        "disable_env": "HERMES_ENABLED=false",
        "base_url_env": "HERMES_BASE_URL=http://127.0.0.1:8642",
        "restart_backend": "Restart Omnix backend after changing Hermes env values.",
    }


def get_legacy_settings_payload() -> dict[str, Any]:
    from app.settings.access import load_secrets, load_settings

    settings = _deep_copy(load_settings())
    secrets = load_secrets()
    api_keys = _safe_dict(secrets.get("api_keys"))

    from app.providers.catalog import API_KEY, providers_with

    for key in providers_with(API_KEY):
        secret_key = str(api_keys.get(key) or "")
        provider_settings = _safe_dict(settings.get(key))
        if secret_key:
            provider_settings["api_key"] = secret_key
            settings[key] = provider_settings
        if provider_settings.get("api_key"):
            provider_settings["api_key"] = _masked_api_key(str(provider_settings["api_key"]))

    return settings


def get_settings_payload() -> SettingsPayload:
    from app.settings.access import load_settings

    settings = load_settings()
    image = _safe_dict(settings.get("image"))
    visual = _safe_dict(settings.get("rpg_visual"))
    return SettingsPayload(
        provider=str(settings.get("provider") or "lmstudio"),
        audio_provider_tts=str(settings.get("audio_provider_tts") or ""),
        audio_provider_stt=str(settings.get("audio_provider_stt") or ""),
        image_enabled=bool(image.get("enabled")),
        rpg_visual_enabled=bool(visual.get("enabled")),
        worker_urls={
            "tts": str(settings.get("tts_worker_url") or ""),
            "stt": str(settings.get("stt_worker_url") or ""),
            "image": str(settings.get("image_worker_url") or ""),
        },
        hermes_status=_hermes_status_payload(),
        hermes_commands=_hermes_commands_payload(),
        settings=get_legacy_settings_payload(),
    )


def apply_settings_payload(
    settings: dict[str, Any],
    secrets: dict[str, Any],
    data: dict[str, Any],
) -> bool:
    """Apply a legacy settings request in memory and report whether secrets changed.

    PostgreSQL authority stores application settings as one document while provider
    secrets live in a separate OS-protected store. Keeping mutation separate from
    persistence lets the SCC adapter perform one atomic settings write and avoid
    touching the secret store for ordinary provider/configuration changes.
    """

    from app.config.defaults import DEFAULT_SETTINGS

    secrets_changed = False

    if "provider" in data:
        settings["provider"] = data["provider"]
    if "global_system_prompt" in data:
        settings["global_system_prompt"] = data["global_system_prompt"]
    if "lmstudio" in data:
        _merge_settings_section(settings, "lmstudio", data["lmstudio"], DEFAULT_SETTINGS["lmstudio"])

    from app.providers.catalog import API_KEY, providers_with

    # Providers that need an API key keep it in the secret store, never in
    # settings; a masked value ("***...") means unchanged.
    for provider_id in providers_with(API_KEY):
        if provider_id not in data:
            continue
        incoming = _safe_dict(data[provider_id])
        api_key = str(incoming.get("api_key") or "")
        if "api_key" in incoming and not api_key.startswith("***"):
            api_keys = secrets.setdefault("api_keys", {})
            if str(api_keys.get(provider_id) or "") != api_key:
                if api_key:
                    api_keys[provider_id] = api_key
                else:
                    api_keys.pop(provider_id, None)
                secrets_changed = True
        _merge_settings_section(
            settings,
            provider_id,
            {key: value for key, value in incoming.items() if key != "api_key"},
            _without_api_key(DEFAULT_SETTINGS[provider_id]),
        )

    if "llamacpp" in data:
        _merge_settings_section(settings, "llamacpp", data["llamacpp"], DEFAULT_SETTINGS["llamacpp"])

    for key in ["audio_provider_tts", "audio_provider_stt", "tts_worker_url", "stt_worker_url", "image_worker_url"]:
        if key in data:
            settings[key] = data[key]

    for key in ["faster-qwen3-tts", "parakeet", "image", "rpg_visual"]:
        if key in data:
            _merge_settings_section(settings, key, data[key], _safe_dict(DEFAULT_SETTINGS.get(key)))

    return secrets_changed


def save_settings_payload(data: dict[str, Any]) -> SettingsSaveResponse:
    from app.settings.access import load_secrets, load_settings, save_secrets, save_settings

    from app.platform.voice.audio_cache import invalidate_changed_audio_caches

    settings = load_settings()
    previous_settings = _deep_copy(settings)
    secrets = load_secrets()
    secrets_changed = apply_settings_payload(settings, secrets, data)

    if secrets_changed:
        save_secrets(secrets)
    save_settings(settings)
    invalidate_changed_audio_caches(previous_settings, settings)
    return SettingsSaveResponse()
