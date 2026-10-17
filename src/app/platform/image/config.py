"""Global image configuration helpers."""
from __future__ import annotations

from app.config.env import environment

import os
from typing import Any, Dict

from app.platform.image.providers.registry import is_supported_image_provider
from app.runtime.paths import MODELS_DIR
from app.settings.access import load_settings

DEFAULT_MODEL_DIR = os.path.join(MODELS_DIR, "image")


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _truthy(value: Any) -> bool:
    return _safe_str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def is_image_generation_enabled() -> bool:
    """Image generation is opt-in because local models can consume substantial VRAM."""

    return _truthy(environment().get("OMNIX_IMAGE_ENABLED", "0"))


def get_image_settings() -> Dict[str, Any]:
    settings = load_settings()
    image_cfg = _safe_dict(settings.get("image"))
    if image_cfg:
        return image_cfg

    # Migration fallback to RPG visual config until everything is moved.
    rpg_visual = _safe_dict(settings.get("rpg_visual"))
    if rpg_visual:
        return rpg_visual

    return {}


def get_active_image_provider_name() -> str:
    if not is_image_generation_enabled():
        return "mock"

    image_cfg = get_image_settings()
    provider = _safe_str(image_cfg.get("provider")).strip().lower()
    if not provider:
        return "flux_klein"
    if is_supported_image_provider(provider):
        return provider
    return "flux_klein"


def get_provider_config(provider_name: str) -> Dict[str, Any]:
    image_cfg = get_image_settings()
    provider_name = _safe_str(provider_name).strip().lower() or get_active_image_provider_name()
    if not is_supported_image_provider(provider_name):
        return {}
    return _safe_dict(image_cfg.get(provider_name))
