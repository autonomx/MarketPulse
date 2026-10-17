"""Backend-owned Chat and Character memory scope resolution."""
from __future__ import annotations

from app.config.env import environment
from app.conversation.contracts import DEFAULT_PROFILE_ID, DEFAULT_WORKSPACE_ID

import re
from typing import Any

from app.conversation.memory_contracts import MemoryOwnerType, MemoryScope, MemoryScopeContext, SYSTEM_MEMORY_OWNER_ID

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")


def _trusted_identifier(value: str | None, fallback: str | None = None) -> str | None:
    text = (value or "").strip()
    if not text:
        return fallback
    if not _IDENTIFIER_PATTERN.fullmatch(text):
        raise ValueError("memory identifiers must use stable local identifier characters")
    return text


def resolve_chat_scope(
    session_id: str,
    *,
    profile_id: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
    owner_type: MemoryOwnerType = "system",
    owner_id: str = SYSTEM_MEMORY_OWNER_ID,
) -> MemoryScopeContext:
    """Resolve owner and scope from server-owned inputs, never arbitrary request data."""

    resolved_profile = _trusted_identifier(
        profile_id or environment().get("OMNIX_CHAT_PROFILE_ID"),
        DEFAULT_PROFILE_ID,
    )
    resolved_workspace = _trusted_identifier(
        workspace_id or environment().get("OMNIX_CHAT_WORKSPACE_ID"),
        DEFAULT_WORKSPACE_ID,
    )
    resolved_owner_id = _trusted_identifier(owner_id)
    if owner_type == "system" and resolved_owner_id != SYSTEM_MEMORY_OWNER_ID:
        raise ValueError("system memory must use the System Assistant owner")
    if owner_type == "character" and resolved_owner_id == SYSTEM_MEMORY_OWNER_ID:
        raise ValueError("character memory requires a character owner")
    return MemoryScopeContext(
        profile_id=resolved_profile or DEFAULT_PROFILE_ID,
        workspace_id=resolved_workspace or DEFAULT_WORKSPACE_ID,
        project_id=_trusted_identifier(project_id),
        session_id=_trusted_identifier(session_id) or session_id,
        owner_type=owner_type,
        owner_id=resolved_owner_id or SYSTEM_MEMORY_OWNER_ID,
    )


def resolve_session_memory_scope(session: Any) -> MemoryScopeContext:
    """Resolve the active memory owner from a persisted server-side Chat session."""

    interaction_mode = getattr(session, "interaction_mode", "system")
    character_id = getattr(session, "character_id", None)
    owner_type: MemoryOwnerType = "character" if interaction_mode == "character" else "system"
    owner_id = str(character_id or "") if owner_type == "character" else SYSTEM_MEMORY_OWNER_ID
    if owner_type == "character" and not owner_id:
        raise ValueError("character session is missing character_id")
    return resolve_chat_scope(
        getattr(session, "id"),
        profile_id=getattr(session, "profile_id", None),
        workspace_id=getattr(session, "workspace_id", None),
        project_id=getattr(session, "project_id", None),
        owner_type=owner_type,
        owner_id=owner_id,
    )


def scope_id_for(scope: MemoryScope, context: MemoryScopeContext) -> str | None:
    if scope == "global":
        return context.profile_id
    if scope == "workspace":
        return context.workspace_id
    if scope == "project":
        return context.project_id
    return context.session_id
