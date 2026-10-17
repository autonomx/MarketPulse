"""Global image model lifecycle helpers (IMG-6)."""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Dict

from app.platform.image.config import get_active_image_provider_name, get_provider_config
from app.platform.image.providers.registry import (
    get_image_provider_definition,
    get_image_provider_keys,
    is_supported_image_provider,
)

_PROVIDER_CACHE: OrderedDict[str, tuple[Any, float]] = OrderedDict()
_PROVIDER_LOCK = threading.RLock()
# The device's image model-owner lease, held while any provider is cached.
_RESIDENCY: Any = None
_LOG = logging.getLogger(__name__)
_LIFECYCLE_LOCK = threading.RLock()
MAX_CACHED_IMAGE_PROVIDERS = 4
IMAGE_PROVIDER_CACHE_TTL_SECONDS = 24 * 60 * 60.0
_GIB = float(1024**3)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _provider_runtime_status(provider: Any) -> Dict[str, Any]:
    reader = getattr(provider, "runtime_status", None)
    if not callable(reader):
        return {}
    try:
        status = reader()
        return status if isinstance(status, dict) else {}
    except Exception:
        return {}


def _unload_instances(providers: Dict[str, Any]) -> list[str]:
    unloaded: list[str] = []
    for provider_name, provider in providers.items():
        try:
            if hasattr(provider, "unload"):
                provider.unload()
        except Exception:
            _LOG.debug("suppressed error in %s", "_unload_instances", exc_info=True)
        unloaded.append(provider_name)
    return unloaded


def _hold_residency() -> None:
    """Own the device's image model slot before a provider can load weights.

    One process per device holds image models; a second process is refused
    instead of loading a competing copy into the same GPU. Without a
    configured permit service (tests, single-process tools) nothing is held.
    """
    global _RESIDENCY
    from app.persistence.device_permits import DevicePermitError, default_device_permit_service

    service = default_device_permit_service()
    with _PROVIDER_LOCK:
        if service is None or _RESIDENCY is not None:
            return
        try:
            _RESIDENCY = service.hold_model_owner(
                "image", holder_id=f"image:{os.getpid()}", process_role="image",
                on_lost=_residency_lost,
            )
        except DevicePermitError as exc:
            raise RuntimeError(f"image_model_owned_elsewhere:{exc}") from exc


def _release_residency_if_idle() -> None:
    global _RESIDENCY
    with _PROVIDER_LOCK:
        if _PROVIDER_CACHE or _RESIDENCY is None:
            return
        guard, _RESIDENCY = _RESIDENCY, None
    try:
        guard.close()
    except Exception:
        _LOG.warning("image model-owner lease release failed", exc_info=True)


def _residency_lost() -> None:
    """The lease expired or was taken over: stop using the GPU's image slot."""
    global _RESIDENCY
    with _PROVIDER_LOCK:
        # Called from the guard's renewal thread, which has already stopped.
        _RESIDENCY = None
    _LOG.error("image model-owner lease lost; unloading image providers")
    _unload_instances(_pop_cached_providers())


def _pop_cached_providers(*, keep: str = "") -> Dict[str, Any]:
    keep = _safe_str(keep).strip().lower()
    with _PROVIDER_LOCK:
        selected = {
            provider_name: value[0]
            for provider_name, value in _PROVIDER_CACHE.items()
            if not keep or provider_name != keep
        }
        for provider_name in selected:
            _PROVIDER_CACHE.pop(provider_name, None)
    return selected


def _expire_cached_providers() -> None:
    now = time.monotonic()
    with _PROVIDER_LOCK:
        expired = [
            name
            for name, (_provider, expires_at) in _PROVIDER_CACHE.items()
            if expires_at <= now
        ]
        providers = [
            _PROVIDER_CACHE.pop(name)[0]
            for name in expired
        ]
    _unload_instances({str(index): provider for index, provider in enumerate(providers)})
    if providers:
        _release_residency_if_idle()


def _validate_load_budget(provider_name: str) -> None:
    definition = get_image_provider_definition(provider_name) or {}
    if bool(definition.get("default_cpu_offload")):
        return
    minimum = definition.get("min_load_free_gib")
    if minimum is None:
        return
    try:
        import torch
    except Exception:
        _LOG.debug("suppressed error in %s", "_validate_load_budget", exc_info=True)
        return
    if not torch.cuda.is_available():
        return
    try:
        free_bytes, total_bytes = torch.cuda.mem_get_info()
    except Exception:
        _LOG.debug("suppressed error in %s", "_validate_load_budget", exc_info=True)
        return
    free_gib = free_bytes / _GIB
    total_gib = total_bytes / _GIB
    required_gib = float(minimum)
    if free_gib < required_gib:
        raise RuntimeError(
            f"{provider_name}_insufficient_vram:"
            f"free_gib={free_gib:.2f} required_gib={required_gib:.2f} "
            f"total_gib={total_gib:.2f}; unload other GPU models before loading"
        )


def get_cached_provider(provider_name: str | None = None):
    provider_name = _safe_str(provider_name).strip().lower() or get_active_image_provider_name()
    _expire_cached_providers()
    with _PROVIDER_LOCK:
        value = _PROVIDER_CACHE.get(provider_name)
        if value is None:
            return None
        _PROVIDER_CACHE.move_to_end(provider_name)
        _PROVIDER_CACHE[provider_name] = (
            value[0],
            time.monotonic() + IMAGE_PROVIDER_CACHE_TTL_SECONDS,
        )
        return value[0]


def get_or_create_image_provider(provider_name: str | None = None):
    provider_name = _safe_str(provider_name).strip().lower() or "flux_klein"
    _expire_cached_providers()
    evicted: Any = None
    with _PROVIDER_LOCK:
        cached = _PROVIDER_CACHE.get(provider_name)
        if cached is None:
            _hold_residency()
            try:
                provider = _build_provider(provider_name)
            except Exception:
                _release_residency_if_idle()
                raise
            if len(_PROVIDER_CACHE) >= MAX_CACHED_IMAGE_PROVIDERS:
                _evicted_name, evicted_value = _PROVIDER_CACHE.popitem(last=False)
                evicted = evicted_value[0]
            _PROVIDER_CACHE[provider_name] = (
                provider,
                time.monotonic() + IMAGE_PROVIDER_CACHE_TTL_SECONDS,
            )
        else:
            provider = cached[0]
            _PROVIDER_CACHE.move_to_end(provider_name)
            _PROVIDER_CACHE[provider_name] = (
                provider,
                time.monotonic() + IMAGE_PROVIDER_CACHE_TTL_SECONDS,
            )
    if evicted is not None:
        _unload_instances({"evicted": evicted})
    return provider


def _build_provider(provider_name: str):
    provider_name = _safe_str(provider_name).strip().lower() or "flux_klein"
    if not is_supported_image_provider(provider_name):
        raise RuntimeError(f"unsupported_image_provider:{provider_name}")
    config = get_provider_config(provider_name)

    if provider_name == "flux_klein":
        from app.platform.image.providers.flux_klein_provider import FluxKleinImageProvider

        return FluxKleinImageProvider(config)
    if provider_name in {"krea2_turbo", "z_image_turbo"}:
        from app.platform.image.providers.diffusers_turbo_provider import DiffusersTurboImageProvider

        return DiffusersTurboImageProvider(provider_name, config)
    if provider_name == "mock":
        from app.platform.image.providers.mock_provider import MockImageProvider

        return MockImageProvider(config)

    raise RuntimeError(f"unsupported_image_provider:{provider_name}")


def _provider_is_loaded_instance(provider: Any) -> bool:
    checker = getattr(provider, "is_loaded", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return provider is not None


def is_image_provider_loaded(provider_name: str | None = None) -> bool:
    provider_name = _safe_str(provider_name).strip().lower() or get_active_image_provider_name()
    return _provider_is_loaded_instance(get_cached_provider(provider_name))


def load_image_provider(provider_name: str | None = None) -> Dict[str, Any]:
    """Atomically switch to one resident image provider and load it."""

    provider_name = _safe_str(provider_name).strip().lower() or get_active_image_provider_name()
    with _LIFECYCLE_LOCK:
        _unload_instances(_pop_cached_providers(keep=provider_name))
        provider = get_or_create_image_provider(provider_name)
        try:
            _validate_load_budget(provider_name)
            provider.load()
        except Exception:
            with _PROVIDER_LOCK:
                cached = _PROVIDER_CACHE.get(provider_name)
                if cached is not None and cached[0] is provider:
                    _PROVIDER_CACHE.pop(provider_name, None)
            with contextlib.suppress(Exception):
                provider.unload()
            _release_residency_if_idle()
            raise

        result: Dict[str, Any] = {
            "ok": True,
            "provider": provider_name,
            "loaded": is_image_provider_loaded(provider_name),
        }
        runtime = _provider_runtime_status(provider)
        if runtime:
            result["runtime"] = runtime
        return result


def unload_image_provider(provider_name: str | None = None) -> Dict[str, Any]:
    provider_name = _safe_str(provider_name).strip().lower() or get_active_image_provider_name()

    with _LIFECYCLE_LOCK:
        with _PROVIDER_LOCK:
            cached = _PROVIDER_CACHE.pop(provider_name, None)
            provider = cached[0] if cached is not None else None
        if provider is not None and hasattr(provider, "unload"):
            provider.unload()
        _release_residency_if_idle()

    return {
        "ok": True,
        "provider": provider_name,
        "loaded": False,
        "unloaded": provider is not None,
    }


def unload_all_image_providers() -> Dict[str, Any]:
    with _LIFECYCLE_LOCK:
        unloaded = _unload_instances(_pop_cached_providers())
        _release_residency_if_idle()
    return {"ok": True, "unloaded": unloaded}


def get_image_provider_cache_status() -> Dict[str, Any]:
    _expire_cached_providers()
    with _PROVIDER_LOCK:
        providers = {
            name: value[0] for name, value in _PROVIDER_CACHE.items()
        }
    loaded_providers = [
        provider_name
        for provider_name, provider in sorted(providers.items())
        if _provider_is_loaded_instance(provider)
    ]
    runtime = {
        provider_name: status
        for provider_name, provider in sorted(providers.items())
        if (status := _provider_runtime_status(provider))
    }
    result: Dict[str, Any] = {
        "ok": True,
        "loaded_providers": loaded_providers,
        "cached_providers": sorted(list(providers.keys())),
        "known_providers": get_image_provider_keys(),
    }
    if runtime:
        result["runtime"] = runtime
    return result
