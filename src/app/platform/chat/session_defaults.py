"""Settings Control Center defaults for a new chat session (moved from app/platform, PA-2.1)."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.prompts import prompt_template
from app.settings.effective_defaults import effective_llm_route, load_effective_profile, set_if_missing, setting_text
from app.settings.profile_models import SettingsProfile

LEGACY_CHAT_DEFAULT_PROMPT_TEMPLATE = prompt_template(
    'chat.session_defaults.legacy_default_prompt', "1",
    'You are Omnix Assistant. Be helpful, clear, and practical.',
)

_LEGACY_CHAT_DEFAULT_PROMPT = LEGACY_CHAT_DEFAULT_PROMPT_TEMPLATE.text
_PERSONALITY_PROMPTS = {
    "omnix-default": "",
    "default": "",
    "concise": "You are Omnix Assistant. Be direct, concise, and action-oriented. Prefer short answers unless detail is requested.",
    "coach": "You are Omnix Assistant. Be warm, encouraging, and practical. Ask at most one clarifying question when needed.",
    "technical": "You are Omnix Assistant. Be precise, technical, and implementation-focused. Include concrete steps and caveats.",
    "creative": "You are Omnix Assistant. Be imaginative, collaborative, and vivid while staying useful and grounded.",
}


def assistant_default_prompt(profile: SettingsProfile) -> str:
    assistant = profile.assistant
    if assistant.personality_id == "custom":
        return assistant.custom_personality.strip()
    return _PERSONALITY_PROMPTS.get(assistant.personality_id, "")


def apply_chat_session_defaults(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    request = deepcopy(value)
    profile = load_effective_profile()
    provider_id, model_id = effective_llm_route(profile, "chatbot", "chat.generate")
    set_if_missing(request, "provider_id", provider_id)
    set_if_missing(request, "model_id", model_id)

    if request.get("interaction_mode", "system") == "system":
        current_prompt = setting_text(request.get("system_prompt"))
        prompt = assistant_default_prompt(profile)
        if prompt and (not current_prompt or current_prompt == _LEGACY_CHAT_DEFAULT_PROMPT):
            request["system_prompt"] = prompt
        set_if_missing(request, "voice_asset_id", profile.assistant.voice_id)
    return request
