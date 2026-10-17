"""Apply central voice-cloning defaults without replacing explicit job overrides."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.settings.effective_defaults import load_effective_profile


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def apply_voice_cloning_defaults(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    request = deepcopy(value)
    payload = request.get("input_payload")
    payload = deepcopy(payload) if isinstance(payload, dict) else {}
    # The profile model is assembled at runtime from declared sections (PA-2.1).
    profile: Any = load_effective_profile()

    if _missing(payload.get("provider_id")):
        payload["provider_id"] = profile.global_settings.providers.voice_cloning or profile.global_settings.providers.tts
    if _missing(payload.get("language")):
        payload["language"] = profile.voice.cloning_language
    if _missing(payload.get("quality")):
        payload["quality"] = profile.voice.cloning_quality

    request["input_payload"] = payload
    return request
