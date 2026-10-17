"""Typed user-setting registry for the browser settings service."""
from __future__ import annotations

from copy import deepcopy

from app.config.defaults import DEFAULT_SETTINGS

from .service import SettingSpec


_OPENROUTER_DEFAULT = deepcopy(DEFAULT_SETTINGS["openrouter"])
_OPENROUTER_DEFAULT.pop("api_key", None)
_CEREBRAS_DEFAULT = deepcopy(DEFAULT_SETTINGS["cerebras"])
_CEREBRAS_DEFAULT.pop("api_key", None)

CORE_SETTING_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec("provider", str, DEFAULT_SETTINGS["provider"]),
    SettingSpec("global_system_prompt", str, DEFAULT_SETTINGS["global_system_prompt"]),
    SettingSpec("lmstudio", dict, deepcopy(DEFAULT_SETTINGS["lmstudio"])),
    SettingSpec("openrouter", dict, _OPENROUTER_DEFAULT),
    SettingSpec("cerebras", dict, _CEREBRAS_DEFAULT),
    SettingSpec("llamacpp", dict, deepcopy(DEFAULT_SETTINGS["llamacpp"])),
    SettingSpec("audio_provider_tts", str, DEFAULT_SETTINGS["audio_provider_tts"]),
    SettingSpec("audio_provider_stt", str, DEFAULT_SETTINGS["audio_provider_stt"]),
    SettingSpec("tts_worker_url", str, ""),
    SettingSpec("stt_worker_url", str, ""),
    SettingSpec("image_worker_url", str, ""),
    SettingSpec("faster-qwen3-tts", dict, deepcopy(DEFAULT_SETTINGS["faster-qwen3-tts"])),
    SettingSpec("fish-s2-pro", dict, deepcopy(DEFAULT_SETTINGS["fish-s2-pro"])),
    SettingSpec("parakeet", dict, deepcopy(DEFAULT_SETTINGS["parakeet"])),
    SettingSpec("image", dict, deepcopy(DEFAULT_SETTINGS["image"])),
    SettingSpec("rpg_visual", dict, deepcopy(DEFAULT_SETTINGS["rpg_visual"])),
    SettingSpec("settings_control_center", dict, {}),
    SettingSpec("trading_market_data", dict, {}),
)


def core_setting_specs() -> tuple[SettingSpec, ...]:
    return CORE_SETTING_SPECS
