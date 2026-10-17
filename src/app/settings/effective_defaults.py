"""Resolve Settings Control Center defaults at runtime without overriding explicit choices."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, TypeVar

from pydantic import BaseModel

from app.settings.access import load_settings

from app.settings.profile_experience import AgentRunSettingsProfile
from app.settings.profile_models import SettingsProfile
from app.settings.profile_repository import load_settings_profile

SectionT = TypeVar("SectionT", bound=BaseModel)


_LEGACY_STORY_TONE = "Cozy"
_LEGACY_STORY_STYLE = "Lyrical & Descriptive"
_LEGACY_PODCAST_DEFAULTS: dict[str, Any] = {
    "format": "debate",
    "duration_minutes": 20,
    "tone": "Professional",
    "language": "English (US)",
    "generation_style": "automatic",
}


def load_effective_profile() -> SettingsProfile:
    return load_settings_profile(load_settings(allow_defaults_without_service=True))


def module_settings(field: str, model: type[SectionT]) -> SectionT:
    """A module's section of the effective profile, as the model its ``declarations.py`` declares (PA-2.1).

    The profile is composed from the declared sections at startup, so a type
    checker cannot see ``profile.<field>``; this reads it with its type.
    """
    section = getattr(load_effective_profile(), field)
    if not isinstance(section, model):
        raise TypeError(f"settings section {field} is {type(section).__name__}, not {model.__name__}")
    return section


def agent_run_settings() -> AgentRunSettingsProfile:
    """Default agent run limits and provider prices from the saved settings."""
    return load_effective_profile().agent_runs


def setting_text(value: Any) -> str:
    return str(value or "").strip()


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def set_if_missing(payload: dict[str, Any], key: str, value: Any) -> None:
    if not _missing(value) and _missing(payload.get(key)):
        payload[key] = deepcopy(value)


def _replace_legacy_default(payload: dict[str, Any], key: str, legacy: Any, value: Any) -> None:
    current = payload.get(key)
    if not _missing(value) and (_missing(current) or current == legacy):
        payload[key] = deepcopy(value)


def _override_value(row: dict[str, Any], camel: str, snake: str) -> str:
    return setting_text(row.get(camel) or row.get(snake))


def _configured_provider_model(profile: SettingsProfile, provider_id: str) -> str:
    provider_key = setting_text(provider_id).removeprefix("llm:")
    config_by_provider = {
        "lmstudio": profile.provider_configs.lmstudio.model,
        "openrouter": profile.provider_configs.openrouter.model,
        "cerebras": profile.provider_configs.cerebras.model,
        "chatgpt_codex": profile.provider_configs.chatgpt_codex.model,
        "claude_cli": profile.provider_configs.claude_cli.model,
        "llamacpp": profile.provider_configs.llamacpp.model,
    }
    return setting_text(config_by_provider.get(provider_key))


def effective_llm_route(profile: SettingsProfile, module: str, task: str) -> tuple[str, str]:
    provider_id = profile.global_settings.providers.llm
    model_id = profile.global_settings.models.chat

    if module == "storyteller":
        provider_id = profile.storyteller.provider_id or provider_id
        model_id = profile.storyteller.model_id or profile.global_settings.models.quality or model_id
    elif module == "podcast":
        provider_id = profile.podcast.provider_id or provider_id
        model_id = profile.podcast.model_id or profile.global_settings.models.quality or model_id
    elif "background" in task or "audit" in task:
        model_id = profile.global_settings.models.background or model_id
    elif "fast" in task or "title" in task or "outline" in task:
        model_id = profile.global_settings.models.fast or model_id

    overrides = profile.global_settings.routing.task_overrides
    for key in (task, f"{module}:{task}", module):
        row = overrides.get(key)
        if not isinstance(row, dict):
            continue
        provider_id = _override_value(row, "providerId", "provider_id") or provider_id
        model_id = _override_value(row, "modelId", "model_id") or model_id
        break
    if not model_id:
        model_id = _configured_provider_model(profile, provider_id)
    return provider_id, model_id


def _apply_storyteller_defaults(payload: dict[str, Any], profile: SettingsProfile, task: str) -> None:
    provider_id, model_id = effective_llm_route(profile, "storyteller", task)
    set_if_missing(payload, "provider_id", provider_id)
    set_if_missing(payload, "model_id", model_id)
    _replace_legacy_default(payload, "tone", _LEGACY_STORY_TONE, profile.storyteller.tone)
    _replace_legacy_default(payload, "writing_style", _LEGACY_STORY_STYLE, profile.storyteller.writing_style)


def _apply_podcast_defaults(payload: dict[str, Any], profile: SettingsProfile) -> None:
    defaults = profile.podcast
    _replace_legacy_default(payload, "format", _LEGACY_PODCAST_DEFAULTS["format"], defaults.format)
    _replace_legacy_default(payload, "duration_minutes", _LEGACY_PODCAST_DEFAULTS["duration_minutes"], defaults.duration_minutes)
    _replace_legacy_default(payload, "tone", _LEGACY_PODCAST_DEFAULTS["tone"], defaults.tone)
    _replace_legacy_default(payload, "language", _LEGACY_PODCAST_DEFAULTS["language"], defaults.language)
    _replace_legacy_default(payload, "generation_style", _LEGACY_PODCAST_DEFAULTS["generation_style"], defaults.generation_style)
    output = payload.get("output_settings") if isinstance(payload.get("output_settings"), dict) else {}
    output = deepcopy(output)
    _replace_legacy_default(output, "stability", 0.72, defaults.stability)
    _replace_legacy_default(output, "similarity", 0.78, defaults.similarity)
    payload["output_settings"] = output
    if not payload.get("audio_effects") or payload.get("audio_effects") == ["Compression", "De-esser"]:
        payload["audio_effects"] = list(defaults.effects)


def _apply_voice_defaults(payload: dict[str, Any], profile: SettingsProfile) -> None:
    set_if_missing(payload, "provider_id", profile.global_settings.providers.tts)
    set_if_missing(payload, "language", profile.voice.language)
    output = payload.get("output_settings") if isinstance(payload.get("output_settings"), dict) else {}
    output = deepcopy(output)
    for key, value in {
        "stability": profile.voice.stability,
        "similarity": profile.voice.similarity,
        "style": profile.voice.style,
        "speed": profile.voice.speed,
        "pitch": profile.voice.pitch,
        "volume": profile.voice.volume,
    }.items():
        set_if_missing(output, key, value)
    payload["output_settings"] = output
    if not payload.get("audio_effects"):
        payload["audio_effects"] = list(profile.voice.effects)


def _apply_stt_defaults(payload: dict[str, Any], profile: SettingsProfile) -> None:
    set_if_missing(payload, "provider_id", profile.global_settings.providers.stt)
    set_if_missing(payload, "language", profile.stt.language)
    set_if_missing(payload, "alignment", profile.stt.alignment)
    set_if_missing(payload, "save_transcript", profile.stt.save_transcript)


def _apply_image_defaults(payload: dict[str, Any], profile: SettingsProfile) -> None:
    set_if_missing(payload, "provider_id", profile.global_settings.providers.image)
    set_if_missing(payload, "width", profile.image.width)
    set_if_missing(payload, "height", profile.image.height)
    set_if_missing(payload, "unload_after_generation", profile.image.unload_after_generation)


def apply_job_defaults(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    request = deepcopy(value)
    payload = request.get("input_payload")
    payload = deepcopy(payload) if isinstance(payload, dict) else {}
    module = setting_text(request.get("module"))
    task = setting_text(request.get("type"))
    resource_class = setting_text(request.get("resource_class"))
    profile = load_effective_profile()

    if module == "storyteller":
        _apply_storyteller_defaults(payload, profile, task)
    elif module == "podcast":
        _apply_podcast_defaults(payload, profile)
    elif module in {"voice", "voice-cloning"}:
        _apply_voice_defaults(payload, profile)
    elif module == "stt":
        _apply_stt_defaults(payload, profile)
    elif module == "image-generation":
        _apply_image_defaults(payload, profile)
    elif module == "character-avatar":
        # Avatar packs require image-to-image variants for mouth, blink, and
        # expression frames. FLUX is the only compatible local provider in the
        # current registry, so do not inherit a text-to-image-only selection.
        set_if_missing(payload, "provider_id", "image:flux_klein")
        set_if_missing(payload, "unload_after_generation", False)
        _apply_image_defaults(payload, profile)

    if resource_class == "gpu:llm":
        provider_id, model_id = effective_llm_route(profile, module, task)
        set_if_missing(payload, "provider_id", provider_id)
        set_if_missing(payload, "model_id", model_id)

    request["input_payload"] = payload
    return request
