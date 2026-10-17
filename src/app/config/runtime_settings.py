"""Process-scoped settings/secrets bridge used during shared.py retirement.

Kernel/provider packages depend on this neutral contract. Composition installs
the authoritative loaders; the bridge never imports app.shared.
"""
from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any, Callable

_LOCK = RLock()
_settings_loader: Callable[[], dict[str, Any]] | None = None
_secrets_loader: Callable[[], dict[str, Any]] | None = None
_provider_resolver: Callable[[str | None], Any] | None = None
_system_prompt_loader: Callable[[], str] | None = None


def install_runtime_settings_bridge(
    *,
    settings_loader: Callable[[], dict[str, Any]],
    secrets_loader: Callable[[], dict[str, Any]],
    provider_resolver: Callable[[str | None], Any] | None = None,
    system_prompt_loader: Callable[[], str] | None = None,
) -> None:
    global _settings_loader, _secrets_loader, _provider_resolver, _system_prompt_loader
    with _LOCK:
        _settings_loader = settings_loader
        _secrets_loader = secrets_loader
        _provider_resolver = provider_resolver
        _system_prompt_loader = system_prompt_loader


def clear_runtime_settings_bridge_for_tests() -> None:
    global _settings_loader, _secrets_loader, _provider_resolver, _system_prompt_loader
    with _LOCK:
        _settings_loader = None
        _secrets_loader = None
        _provider_resolver = None
        _system_prompt_loader = None


def load_runtime_settings() -> dict[str, Any]:
    with _LOCK:
        loader = _settings_loader
    if loader is None:
        return {}
    return deepcopy(loader() or {})


def load_runtime_secrets() -> dict[str, Any]:
    with _LOCK:
        loader = _secrets_loader
    if loader is None:
        return {"api_keys": {}}
    return deepcopy(loader() or {"api_keys": {}})


def resolve_runtime_provider(provider_name: str | None = None) -> Any:
    with _LOCK:
        resolver = _provider_resolver
    if resolver is None:
        raise RuntimeError("Provider resolver is not installed")
    return resolver(provider_name)


def runtime_system_prompt(default: str = "You are a helpful AI assistant.") -> str:
    with _LOCK:
        loader = _system_prompt_loader
    if loader is None:
        return default
    value = str(loader() or "").strip()
    return value or default
