"""Chat's memory, without importing memory (ADR-0016, PA-1.3, PA-3.2).

Assistant memory implements ``CHAT_MEMORY``: the runtime settings chat reads
(compaction, history recall, section budgets, transcript retention), the
memory a prompt carries, explicit memory commands, and session snapshots.
Without it chat serves turns with no memory: no memory in prompts, memory
commands are not recognized, and the settings take memory's defaults.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.runtime.ports import Port, optional


class ChatMemory(Protocol):
    def runtime_settings(self) -> Any:
        """The memory runtime settings (an object with the fields ``ChatMemorySettings`` names)."""

    def service(self) -> Any:
        """The memory service chat's stores pass on to memory commands and prompt resolution."""

    def resolve_prompt_memory(self, session: Any, **options: Any) -> tuple[list[Any], dict[str, Any]]:
        """The memory items a session's prompt carries, and their diagnostics."""

    def parse_command(self, content: str) -> Any | None:
        """An explicit memory command in a user message, or ``None``."""

    def execute_command(self, store: Any, service: Any, session_id: str, user_message_id: str, command: Any) -> Any:
        """Run a parsed memory command for a session's message."""

    def create_session_snapshot(self, session: Any, *, token_budget: int) -> Any:
        """A frozen snapshot of the memory a session may read (id, revision, items, created_at)."""


CHAT_MEMORY: Port[ChatMemory] = Port("chat.memory", ChatMemory, "at_most_one")


@dataclass(frozen=True)
class ChatMemorySettings:
    """Memory's defaults for the settings chat reads, used when memory is absent."""

    curated_memory_enabled: bool = False
    history_recall_enabled: bool = False
    compaction_enabled: bool = False
    transcript_retention_enabled: bool = True
    memory_token_budget: int = 4_000
    history_token_budget: int = 8_000


class MemoryUnavailable(RuntimeError):
    """Chat asked for memory, and the assistant-memory module is not enabled."""


def chat_memory() -> ChatMemory | None:
    return optional(CHAT_MEMORY)


def memory_runtime_settings() -> Any:
    memory = chat_memory()
    return memory.runtime_settings() if memory is not None else ChatMemorySettings()


def chat_memory_service() -> Any:
    """The memory service; chat's stores take this as their default ``memory_service_factory``."""
    memory = chat_memory()
    if memory is None:
        raise MemoryUnavailable("assistant memory is not enabled")
    return memory.service()


def resolve_prompt_memory(session: Any, **options: Any) -> tuple[list[Any], dict[str, Any]]:
    memory = chat_memory()
    if memory is None:
        return [], {"memory_enabled": False, "status": "memory_unavailable", "selected_memory_ids": [],
                    "selected_memory_count": 0}
    return memory.resolve_prompt_memory(session, **options)


def parse_memory_command(content: str) -> Any | None:
    memory = chat_memory()
    return memory.parse_command(content) if memory is not None else None


def execute_memory_command(store: Any, service: Any, session_id: str, user_message_id: str, command: Any) -> Any:
    memory = chat_memory()
    if memory is None:
        raise MemoryUnavailable("assistant memory is not enabled")
    return memory.execute_command(store, service, session_id, user_message_id, command)


__all__ = [
    "CHAT_MEMORY",
    "ChatMemory",
    "ChatMemorySettings",
    "MemoryUnavailable",
    "chat_memory",
    "chat_memory_service",
    "execute_memory_command",
    "memory_runtime_settings",
    "parse_memory_command",
    "resolve_prompt_memory",
]
