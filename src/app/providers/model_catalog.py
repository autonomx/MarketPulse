"""Facts providers advertise about their models: the context window (WP-5.7).

A chat turn must not wait on a provider's model list (a remote call for
hosted providers), so lookups read a per-provider cache. A miss or a stale
entry starts one background refresh for that provider and the lookup answers
from what is cached (``None`` the first time); a failed refresh is cached
too, so a provider that cannot list models is asked again only after the TTL.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

CACHE_SECONDS = 600.0
# One entry per configured provider; more than this means ids are being invented.
MAX_PROVIDERS = 32

ProviderLookup = Callable[[str], Any]

_LOCK = threading.Lock()
_WINDOWS: dict[str, tuple[float, dict[str, int]]] = {}
_REFRESHING: set[str] = set()


def _default_provider(provider_id: str) -> Any:
    from app.providers.service import get_provider

    return get_provider(provider_id)


def advertised_context_window(
    provider_id: str | None,
    model_id: str | None,
    *,
    provider_lookup: ProviderLookup = _default_provider,
    background: bool = True,
) -> int | None:
    """The model's advertised context window in tokens, or None while unknown."""
    if not provider_id or not model_id:
        return None
    now = time.monotonic()
    with _LOCK:
        entry = _WINDOWS.get(provider_id)
        stale = entry is None or now - entry[0] >= CACHE_SECONDS
        start = stale and provider_id not in _REFRESHING
        if start:
            _REFRESHING.add(provider_id)
    if start:
        if background:
            threading.Thread(
                target=_refresh, args=(provider_id, provider_lookup),
                name=f"omnix-model-catalog-{provider_id}", daemon=True,
            ).start()
        else:
            _refresh(provider_id, provider_lookup)
            with _LOCK:
                entry = _WINDOWS.get(provider_id)
    return entry[1].get(model_id) if entry is not None else None


def _refresh(provider_id: str, provider_lookup: ProviderLookup) -> None:
    windows: dict[str, int] = {}
    try:
        provider = provider_lookup(provider_id)
        for model in provider.get_models() if provider is not None else ():
            length = getattr(model, "context_length", None)
            if isinstance(length, int) and not isinstance(length, bool) and length > 0:
                windows[str(model.id)] = length
    except Exception:
        logger.debug("model_catalog_refresh_failed provider=%s", provider_id, exc_info=True)
    finally:
        with _LOCK:
            _WINDOWS[provider_id] = (time.monotonic(), windows)
            while len(_WINDOWS) > MAX_PROVIDERS:
                _WINDOWS.pop(min(_WINDOWS, key=lambda key: _WINDOWS[key][0]))
            _REFRESHING.discard(provider_id)


def clear_model_catalog() -> None:
    """Forget cached model facts (tests, provider reconfiguration)."""
    with _LOCK:
        _WINDOWS.clear()


__all__ = ["CACHE_SECONDS", "advertised_context_window", "clear_model_catalog"]
