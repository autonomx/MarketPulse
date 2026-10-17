"""Pure server-side Character Mode identity resolution.

The system assistant's identity and the interaction types are the kernel's
(``app.conversation.contracts``, PA-1.3); this resolves characters.
"""
from __future__ import annotations

from typing import Any
from app.config.env import env_str as _env_str

from app.conversation.contracts import (
    CharacterInteractionError,
    CharacterModeDisabledError,
    CharacterResolutionError,
    interaction_identity_hash,
    resolve_system_interaction,
    session_interaction_selection,
)

from .models import (
    CharacterProfileSnapshot,
    InteractionSelection,
    ResolvedInteractionContext,
)

_ALLOWED_SHARED_CATEGORIES = {"preference", "fact", "project", "relationship", "instruction"}


def _env_flag(name: str, default: str = "0") -> bool:
    return (_env_str(name) or default).strip().lower() in {"1", "true", "yes", "on"}


def character_mode_enabled() -> bool:
    return _env_flag("OMNIX_CHARACTER_MODE_ENABLED")


def character_memory_enabled() -> bool:
    return _env_flag("OMNIX_CHARACTER_MEMORY_ENABLED")


def character_shared_memory_enabled() -> bool:
    return _env_flag("OMNIX_CHARACTER_SHARED_MEMORY_ENABLED")


def character_hermes_sync_enabled() -> bool:
    return _env_flag("OMNIX_CHARACTER_HERMES_SYNC_ENABLED")


def resolve_shared_memory_categories(session: object) -> list[str]:
    """Resolve the server-owned System Assistant category allowlist for a session."""

    if getattr(session, "interaction_mode", "system") != "character":
        return []
    if getattr(session, "shared_memory_access", "none") != "read_only":
        return []
    if not character_shared_memory_enabled():
        raise CharacterInteractionError("shared character memory access is disabled")
    character_id = str(getattr(session, "character_id", "") or "")
    if not character_id:
        raise CharacterResolutionError("character session is missing character_id")
    try:
        from .service import default_character_service

        character = default_character_service().resolve_snapshot(character_id)
    except Exception as exc:
        raise CharacterResolutionError("persisted character profile could not be resolved") from exc
    return _validate_shared_memory_policy(character)


def _validate_shared_memory_policy(character: CharacterProfileSnapshot) -> list[str]:
    policy = dict(character.shared_memory_policy or {})
    if policy.get("access") != "read_only":
        raise CharacterInteractionError("character profile does not permit shared memory access")
    raw_categories = policy.get("allowed_categories") or []
    if not isinstance(raw_categories, list):
        raise CharacterInteractionError("character shared memory categories are invalid")
    categories = [str(value) for value in raw_categories if str(value) in _ALLOWED_SHARED_CATEGORIES]
    if not categories:
        raise CharacterInteractionError("character profile has no permitted shared memory categories")
    return sorted(set(categories))


def _validated_identity_policy(character: CharacterProfileSnapshot) -> dict[str, object]:
    policy = dict(character.identity_policy or {})
    if policy.get("may_claim_to_be_human") is True:
        raise CharacterInteractionError("character identity policy cannot permit human identity claims")
    if policy.get("may_claim_real_world_experiences") is True:
        raise CharacterInteractionError("character identity policy cannot permit real-world experience claims")
    if policy.get("disclosure_required") is False:
        raise CharacterInteractionError("character identity disclosure cannot be disabled")
    policy["may_claim_to_be_human"] = False
    policy["may_claim_real_world_experiences"] = False
    policy["disclosure_required"] = True
    return policy


def resolve_interaction_context(
    selection: InteractionSelection,
    *,
    character: CharacterProfileSnapshot | None = None,
) -> ResolvedInteractionContext:
    """Resolve an untrusted selection into a trusted, reproducible identity context."""

    if selection.interaction_mode == "system":
        return resolve_system_interaction(selection)

    if not character_mode_enabled():
        raise CharacterModeDisabledError("Character Mode is disabled")
    if not selection.character_id:
        raise CharacterResolutionError("character mode requires character_id")
    if character is None:
        raise CharacterResolutionError("character profile was not resolved by the server")
    if character.id != selection.character_id:
        raise CharacterResolutionError("resolved character does not match character_id")
    if not character.enabled:
        raise CharacterResolutionError("character profile is disabled")

    # Keep governance validation server-side, but do not turn policy metadata or
    # generic assistant wording into competing model instructions. Character
    # Mode has one authoritative persona prompt: the saved personality prompt.
    _validated_identity_policy(character)
    if selection.shared_memory_access != "none":
        if not character_shared_memory_enabled():
            raise CharacterInteractionError("shared character memory access is disabled")
        _validate_shared_memory_policy(character)
    if (selection.read_memory or selection.write_memory) and not character_memory_enabled():
        raise CharacterInteractionError("character memory is disabled")

    # A character's governed default voice is authoritative for Character Mode.
    # The session selection remains a fallback for legacy profiles without one.
    voice_asset_id = selection.voice_asset_id or character.default_voice_asset_id
    assistant_identity = [character.personality_prompt.strip()]
    payload = {
        "interaction_mode": "character",
        "owner_type": "character",
        "owner_id": character.id,
        "display_name": character.display_name,
        "character_id": character.id,
        "voice_asset_id": voice_asset_id,
        "read_memory": selection.read_memory,
        "write_memory": selection.write_memory,
        "shared_memory_access": selection.shared_memory_access,
        "transcript_policy": selection.transcript_policy,
        "character_profile_version": character.version,
        "assistant_identity": assistant_identity,
    }
    return ResolvedInteractionContext(**payload, effective_identity_hash=interaction_identity_hash(payload))


def resolve_system_session_identity(session: object) -> ResolvedInteractionContext:
    """Resolve a persisted Chat session through backend-owned identity data."""

    selection = session_interaction_selection(session)
    character = None
    if selection.interaction_mode == "character":
        try:
            from .service import default_character_service

            character = default_character_service().resolve_snapshot(selection.character_id or "")
        except Exception as exc:
            raise CharacterResolutionError("persisted character profile could not be resolved") from exc
    return resolve_interaction_context(selection, character=character)


class CharacterChatResolver:
    def mode_enabled(self) -> bool:
        return character_mode_enabled()

    def resolve_snapshot(self, character_id: str) -> Any:
        """Characters' implementation of chat's ``CHARACTER_RESOLVER`` port (PA-1.3)."""
        from .service import default_character_service

        return default_character_service().resolve_snapshot(character_id)

    def resolve_character(self, selection: InteractionSelection, snapshot: Any) -> ResolvedInteractionContext:
        return resolve_interaction_context(selection, character=snapshot)

    def shared_memory_categories(self, session: object) -> list[str]:
        return resolve_shared_memory_categories(session)
