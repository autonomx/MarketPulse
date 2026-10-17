"""Who a chat session speaks as, without importing characters (ADR-0016, PA-1.3).

Chat resolves the system assistant itself (``app.conversation.contracts``).
A character identity comes from the module that owns characters, through the
``CHARACTER_RESOLVER`` port; without it, Character Mode is unavailable and
system sessions work as before.
"""
from __future__ import annotations

from typing import Any, Protocol

from app.conversation.contracts import (
    SYSTEM_ASSISTANT_IDENTITY,
    CharacterModeDisabledError,
    CharacterResolutionError,
    InteractionSelection,
    ResolvedInteractionContext,
    resolve_system_interaction,
    session_interaction_selection,
)
from app.prompts import prompt_template
from app.runtime.ports import Port, optional


class CharacterResolver(Protocol):
    """Resolves Character Mode for chat (implemented by characters)."""

    def mode_enabled(self) -> bool:
        """Whether Character Mode is turned on."""

    def resolve_snapshot(self, character_id: str) -> Any:
        """The character's current version-pinned snapshot; raises when it cannot be resolved."""

    def resolve_character(self, selection: InteractionSelection, snapshot: Any) -> ResolvedInteractionContext:
        """A character-mode selection's trusted identity, from the snapshot it names."""

    def shared_memory_categories(self, session: object) -> list[str]:
        """The System Assistant memory categories a character session may read."""


CHARACTER_RESOLVER: Port[CharacterResolver] = Port("chat.character_resolver", CharacterResolver, "at_most_one")


def _resolver() -> CharacterResolver:
    resolver = optional(CHARACTER_RESOLVER)
    if resolver is None:
        raise CharacterModeDisabledError("Character Mode is disabled")
    return resolver


def character_mode_available() -> bool:
    resolver = optional(CHARACTER_RESOLVER)
    return resolver is not None and resolver.mode_enabled()


def resolve_selection(selection: InteractionSelection) -> tuple[ResolvedInteractionContext, Any]:
    """A requested selection's identity, and the character snapshot for a character selection (else ``None``)."""
    if selection.interaction_mode == "system":
        return resolve_system_interaction(selection), None
    resolver = _resolver()
    snapshot = resolver.resolve_snapshot(selection.character_id or "")
    return resolver.resolve_character(selection, snapshot), snapshot


def resolve_system_session_identity(session: object) -> ResolvedInteractionContext:
    """Resolve a persisted chat session through backend-owned identity data."""
    selection = session_interaction_selection(session)
    if selection.interaction_mode == "system":
        return resolve_system_interaction(selection)
    resolver = _resolver()
    try:
        snapshot = resolver.resolve_snapshot(selection.character_id or "")
    except Exception as exc:
        raise CharacterResolutionError("persisted character profile could not be resolved") from exc
    return resolver.resolve_character(selection, snapshot)


def resolve_shared_memory_categories(session: object) -> list[str]:
    """The System Assistant category allowlist for a session: none outside Character Mode."""
    if getattr(session, "interaction_mode", "system") != "character":
        return []
    if getattr(session, "shared_memory_access", "none") != "read_only":
        return []
    return _resolver().shared_memory_categories(session)


LEGACY_MAYA_SYSTEM_PROMPT_TEMPLATE = prompt_template(
    'chat.session_identity.legacy_maya_system_prompt', "1",
    (
        'You are Maya, a warm, friendly, emotionally aware AI. Keep responses short (1-3 '
        "sentences for voice, 5 for text), match the user's emotional tone, avoid filler and "
        'tangents. Be clear and concise, admit uncertainty when needed, and maintain a natural, '
        'human-like presence.'
    ),
)
LEGACY_MAYA_SYSTEM_PROMPT = LEGACY_MAYA_SYSTEM_PROMPT_TEMPLATE.text


def neutralize_legacy_system_prompt(prompt: str) -> str:
    """Replace only the old built-in Maya default, preserving user custom prompts."""

    text = (prompt or "").strip()
    if text == LEGACY_MAYA_SYSTEM_PROMPT:
        return SYSTEM_ASSISTANT_IDENTITY
    return text or SYSTEM_ASSISTANT_IDENTITY


__all__ = [
    "CHARACTER_RESOLVER",
    "CharacterResolver",
    "LEGACY_MAYA_SYSTEM_PROMPT",
    "character_mode_available",
    "neutralize_legacy_system_prompt",
    "resolve_selection",
    "resolve_shared_memory_categories",
    "resolve_system_session_identity",
]
