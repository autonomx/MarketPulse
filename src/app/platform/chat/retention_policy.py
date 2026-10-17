"""Authoritative privacy and transcript-retention decisions for Chat turns."""
from __future__ import annotations

from typing import Any

from .memory_port import memory_runtime_settings as load_memory_runtime_settings
from app.conversation.privacy import (
    automatic_memory_derivation_allowed,
    private_session,
)

def transcript_retention_allowed(
    session: Any,
    *,
    settings: Any | None = None,
) -> bool:
    """Decide whether new transcript and summary material may be persisted."""

    effective = settings or load_memory_runtime_settings()
    return not private_session(session) and effective.transcript_retention_enabled


def explicit_memory_mutation_allowed(session: Any, command_kind: str) -> bool:
    """Permit privacy-preserving removals but reject private durable writes."""

    if not private_session(session):
        return True
    return command_kind not in {"save", "update"}


__all__ = [
    "automatic_memory_derivation_allowed",
    "explicit_memory_mutation_allowed",
    "private_session",
    "transcript_retention_allowed",
]
