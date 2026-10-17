"""Stable character services consumed by neighboring feature modules."""
from __future__ import annotations

from importlib import import_module
from typing import Any

from app.platform.characters.interaction import (
    InteractionSelection,
    character_mode_enabled,
    resolve_interaction_context,
    resolve_shared_memory_categories,
    resolve_system_session_identity,
)
from app.platform.characters.live_conversation_profile import (
    LiveConversationProfile,
    LiveConversationProfileStore,
    default_live_conversation_profile_store,
)
from app.platform.characters.service import (
    CHARACTER_SNAPSHOT_OBSERVERS,
    CharacterSnapshotObserver,
    default_character_service,
)
from app.platform.characters.session_models import SetSessionInteractionRequest

__all__ = [
    "CHARACTER_SNAPSHOT_OBSERVERS",
    "CharacterSnapshotObserver",
    "InteractionSelection",
    "LiveConversationProfile",
    "LiveConversationProfileStore",
    "SetSessionInteractionRequest",
    "character_mode_enabled",
    "default_character_service",
    "default_live_conversation_profile_store",
    "resolve_interaction_context",
    "resolve_shared_memory_categories",
    "resolve_system_session_identity",
]


# Services apps import through this contract, loaded on first use (ADR-0016).
_LAZY_EXPORTS = {
    "CharacterLiveCallRuntime": "live_call",
    "resolve_live_call_runtime": "live_call",
    "LiveConversationProfileEnvelope": "live_conversation_profile",
    "LiveConversationProfileUpdate": "live_conversation_profile",
    "CharacterDataActionRequest": "management",
    "CharacterDataActionResponse": "management",
    "CharacterDataExport": "management",
    "CharacterManagementService": "management",
    "CharacterConflictError": "repository",
    "CharacterNotFoundError": "repository",
    "CharacterService": "service",
    "character_hermes_sync_enabled": "interaction",
}


def __getattr__(name: str) -> Any:
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"app.platform.characters.{module}"), name)
