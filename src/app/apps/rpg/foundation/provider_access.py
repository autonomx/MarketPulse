"""RPG-owned boundary to the process provider service.

Deterministic RPG packages may depend on this adapter, but not on live provider
implementations directly. Tests can replace these functions without importing
provider packages inside deterministic modules.
"""
from __future__ import annotations

from typing import Any


def chat_completion(*, messages: list[dict[str, Any]], stream: bool = False, **kwargs: Any) -> Any:
    from app.providers.service import chat_completion as provider_chat_completion

    return provider_chat_completion(messages=messages, stream=stream, **kwargs)


def rpg_llm_route() -> tuple[str, str] | None:
    """RPG's provider and model from the settings' model roles (``effective_llm_route``).

    The model is the provider's own name for it (``llm:claude_cli:haiku`` is
    ``haiku``); a model of another provider is not used. None when the
    settings name no provider.
    """
    try:
        from app.settings.access import load_settings
        from app.settings.effective_defaults import effective_llm_route
        from app.settings.profile_repository import load_settings_profile

        provider_id, model_id = effective_llm_route(load_settings_profile(load_settings()), "rpg", "turn")
    except Exception:
        return None
    provider = str(provider_id or "").strip().removeprefix("llm:")
    if not provider:
        return None
    model = str(model_id or "").strip()
    prefix = f"llm:{provider}:"
    if model.startswith(prefix):
        model = model[len(prefix):]
    elif model.startswith("llm:"):
        model = ""
    return provider, model


def routed_provider() -> Any:
    """The provider RPG's model calls use: its settings route, or the app default."""
    from app.providers import service

    route = rpg_llm_route()
    if route is None:
        return service.get_provider()
    return service.get_routed_provider(*route)


def get_provider() -> Any:
    from app.apps.rpg.foundation.llm_gateway_adapter import adapt_base_provider

    return adapt_base_provider(routed_provider())
