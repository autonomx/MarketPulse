from __future__ import annotations

import logging

import time
from typing import Any, Dict, Tuple

from app.settings.access import load_settings

from .base import BaseImageProvider, ImageGenerationResult
from .disabled_provider import DisabledImageProvider
from .registry import (
    build_visual_provider,
    get_visual_provider_runtime_validator,
    has_visual_provider,
    list_visual_provider_keys,
    list_visual_provider_options,
    resolve_visual_provider_key,
)

logger = logging.getLogger(__name__)

_IMAGE_PROVIDER_CACHE: Dict[str, Any] = {}
MAX_CACHED_IMAGE_PROVIDERS = 1
IMAGE_PROVIDER_CACHE_TTL_SECONDS = 24 * 60 * 60.0
_IMAGE_PROVIDER_CACHE_TOUCHED_AT = 0.0


def _expire_image_provider_cache() -> None:
    if (
        _IMAGE_PROVIDER_CACHE.get("entry") is not None
        and time.monotonic() - _IMAGE_PROVIDER_CACHE_TOUCHED_AT
        > IMAGE_PROVIDER_CACHE_TTL_SECONDS
    ):
        unload_image_provider_cache()


def _store_image_provider(cache_key: str, provider_key: str, instance: BaseImageProvider) -> None:
    global _IMAGE_PROVIDER_CACHE_TOUCHED_AT
    if len(_IMAGE_PROVIDER_CACHE) >= MAX_CACHED_IMAGE_PROVIDERS:
        unload_image_provider_cache()
    _IMAGE_PROVIDER_CACHE["entry"] = (cache_key, instance, provider_key)
    _IMAGE_PROVIDER_CACHE_TOUCHED_AT = time.monotonic()


def __getattr__(name: str) -> Any:
    if name == "FluxKleinImageProvider":
        from .flux_klein_provider import FluxKleinImageProvider

        return FluxKleinImageProvider
    raise AttributeError(name)


def _visual_settings() -> Dict[str, Any]:
    settings = load_settings()
    return dict(settings.get("rpg_visual") or {})


def image_generation_enabled() -> bool:
    visual = _visual_settings()
    provider_key = resolve_visual_provider_key(visual)
    return provider_key != "disabled"


def is_image_provider_loaded() -> bool:
    _expire_image_provider_cache()
    return _IMAGE_PROVIDER_CACHE.get("entry") is not None


def get_loaded_image_provider_name() -> str:
    _expire_image_provider_cache()
    entry = _IMAGE_PROVIDER_CACHE.get("entry")
    provider_key = entry[2] if entry else None
    if provider_key:
        return str(provider_key)
    instance = entry[1] if entry else None
    if instance is None:
        return ""
    return str(getattr(instance, "provider_name", "") or "").strip()


def get_loaded_image_provider() -> BaseImageProvider | None:
    _expire_image_provider_cache()
    entry = _IMAGE_PROVIDER_CACHE.get("entry")
    return entry[1] if entry else None


def get_image_provider_cache_key() -> str:
    _expire_image_provider_cache()
    entry = _IMAGE_PROVIDER_CACHE.get("entry")
    return str(entry[0] or "") if entry else ""


def is_loaded_image_provider_ready() -> bool:
    _expire_image_provider_cache()
    entry = _IMAGE_PROVIDER_CACHE.get("entry")
    if not entry:
        return False
    instance = entry[1]
    is_available = getattr(instance, "is_available", None)
    if callable(is_available):
        return bool(is_available())
    return True


def unload_image_provider_cache() -> None:
    global _IMAGE_PROVIDER_CACHE_TOUCHED_AT
    entry = _IMAGE_PROVIDER_CACHE.get("entry")
    instance = entry[1] if entry else None
    if instance is not None:
        try:
            unload = getattr(instance, "unload", None)
            if callable(unload):
                unload()
        except Exception:
            logger.debug("suppressed error in %s", "unload_image_provider_cache", exc_info=True)
    _IMAGE_PROVIDER_CACHE.clear()
    _IMAGE_PROVIDER_CACHE_TOUCHED_AT = 0.0


def get_image_provider() -> BaseImageProvider:
    """
    Backwards-compatible provider accessor.

    Existing routes still import and call this function. Keep it stable while
    the app migrates toward explicit registry-based construction.
    """
    visual = _visual_settings()
    _expire_image_provider_cache()
    provider_key = resolve_visual_provider_key(visual)
    cache_key = f"{provider_key}:{visual!r}"

    if (
        (entry := _IMAGE_PROVIDER_CACHE.get("entry")) is not None
        and entry[0] == cache_key
        and entry[1] is not None
    ):
        global _IMAGE_PROVIDER_CACHE_TOUCHED_AT
        _IMAGE_PROVIDER_CACHE_TOUCHED_AT = time.monotonic()
        return entry[1]

    unload_image_provider_cache()

    selected_key, instance = build_visual_provider(visual)

    _store_image_provider(cache_key, selected_key, instance)
    return instance


def preload_image_provider(force_reload: bool = False) -> BaseImageProvider:
    """
    Force provider materialization so the UI can warm the runtime and VRAM.
    """
    if force_reload:
        unload_image_provider_cache()
    provider = get_image_provider()
    load = getattr(provider, "load", None)
    if callable(load):
        load()
    return provider


def switch_image_provider_runtime(
    *,
    provider_key: str | None,
    enabled: bool = True,
    provider_config: Dict[str, Any] | None = None,
    force_reload: bool = True,
) -> Tuple[str, BaseImageProvider]:
    """
    Runtime-only hot switch. This does not persist settings by itself.
    """
    cfg: Dict[str, Any] = dict(provider_config or {})
    cfg["enabled"] = bool(enabled)
    if provider_key is not None:
        cfg["visual_provider"] = str(provider_key)

    if force_reload:
        unload_image_provider_cache()

    selected_key, provider = build_visual_provider(cfg)
    _store_image_provider(f"runtime:{selected_key}:{cfg!r}", selected_key, provider)
    return selected_key, provider


def get_visual_provider_status_payload() -> Dict[str, Any]:
    provider = get_loaded_image_provider()
    loaded_provider = get_loaded_image_provider_name()
    runtime_status: Dict[str, Any] = {}
    if provider is not None:
        runtime = getattr(provider, "runtime_status", None)
        if callable(runtime):
            try:
                runtime_status = dict(runtime() or {})
            except Exception as exc:
                runtime_status = {"ready": False, "error": str(exc)}

    return {
        "loaded": provider is not None,
        "loaded_provider": loaded_provider,
        "cache_key": get_image_provider_cache_key(),
        "ready": is_loaded_image_provider_ready(),
        "runtime_status": runtime_status,
        "options": list_visual_provider_options(),
    }


__all__ = [
    "BaseImageProvider",
    "ImageGenerationResult",
    "DisabledImageProvider",
    "FluxKleinImageProvider",
    "image_generation_enabled",
    "is_image_provider_loaded",
    "get_loaded_image_provider_name",
    "get_loaded_image_provider",
    "get_image_provider_cache_key",
    "is_loaded_image_provider_ready",
    "unload_image_provider_cache",
    "get_image_provider",
    "preload_image_provider",
    "switch_image_provider_runtime",
    "get_visual_provider_status_payload",
    "build_visual_provider",
    "get_visual_provider_runtime_validator",
    "has_visual_provider",
    "list_visual_provider_keys",
    "list_visual_provider_options",
    "resolve_visual_provider_key",
]
