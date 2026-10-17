"""Deterministic explicit chat memory commands (chat calls them through CHAT_MEMORY, PA-3.2)."""
from __future__ import annotations

import re
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict

from app.platform.chat.contracts import explicit_memory_mutation_allowed
from app.conversation.contracts import ChatSession, ChatSessionMutationPort

from .contracts import (
    MemoryService,
    RefreshSessionMemoryRequest,
    refresh_session_memory,
    resolve_session_memory_scope,
)

MemoryCommandKind = Literal["save", "list", "forget", "refresh", "disable", "update"]


class MemoryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: MemoryCommandKind
    content: str = ""
    scope: Literal["global", "workspace", "project", "session"] = "session"
    category: Literal["preference", "fact", "project", "relationship", "instruction"] = "fact"
    memory_id: str | None = None


class MemoryCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handled: bool = True
    content: str
    command: MemoryCommandKind
    mutated: bool
    memory_ids: list[str] = []


_SAVE_PATTERN = re.compile(
    r"^save\s+as\s+(global|workspace|project|session)\s+"
    r"(preference|fact|project|relationship|instruction)\s*:\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_UPDATE_PATTERN = re.compile(r"^update\s+memory\s+(memory:[A-Za-z0-9_.-]+)\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)


def parse_memory_command(content: str) -> MemoryCommand | None:
    text = " ".join(content.strip().split())
    lowered = text.casefold()
    if lowered in {"what do you remember", "what do you remember?", "list memory", "list memories"}:
        return MemoryCommand(kind="list")
    if lowered in {"refresh memory", "refresh chat memory"}:
        return MemoryCommand(kind="refresh")
    if lowered in {"disable memory for this chat", "start without memory", "turn off memory for this chat"}:
        return MemoryCommand(kind="disable")
    match = _SAVE_PATTERN.match(text)
    if match:
        return MemoryCommand(
            kind="save",
            scope=cast(Any, match.group(1).casefold()),
            category=cast(Any, match.group(2).casefold()),
            content=match.group(3).strip(),
        )
    match = _UPDATE_PATTERN.match(text)
    if match:
        return MemoryCommand(kind="update", memory_id=match.group(1).strip(), content=match.group(2).strip())
    if lowered.startswith("remember that ") and len(text[14:].strip()) >= 3:
        return MemoryCommand(kind="save", content=text[14:].strip(), category="fact")
    if lowered.startswith("remember: ") and len(text[10:].strip()) >= 3:
        return MemoryCommand(kind="save", content=text[10:].strip(), category="fact")
    if lowered.startswith("forget ") and len(text[7:].strip()) >= 3:
        return MemoryCommand(kind="forget", content=text[7:].strip())
    return None


def _scope(session: ChatSession):
    return resolve_session_memory_scope(session)


def _privacy_rejected(session: ChatSession, command: MemoryCommand) -> MemoryCommandResult | None:
    if explicit_memory_mutation_allowed(session, command.kind):
        return None
    return MemoryCommandResult(
        content="Private Chat does not create or update durable memory. No memory was changed.",
        command=command.kind,
        mutated=False,
    )


def _write_rejected(session: ChatSession, command: MemoryCommand) -> MemoryCommandResult | None:
    if session.interaction_mode != "character" or session.write_memory:
        return None
    if command.kind not in {"save", "forget", "update"}:
        return None
    return MemoryCommandResult(
        content="Character memory write is disabled for this Chat. No memory was changed.",
        command=command.kind,
        mutated=False,
    )


def _read_rejected(session: ChatSession, command: MemoryCommand) -> MemoryCommandResult | None:
    if session.interaction_mode != "character" or session.read_memory:
        return None
    if command.kind not in {"list", "refresh"}:
        return None
    return MemoryCommandResult(
        content="Character memory read is disabled for this Chat.",
        command=command.kind,
        mutated=False,
    )


def execute_memory_command(
    store: ChatSessionMutationPort,
    service: MemoryService,
    session_id: str,
    user_message_id: str,
    command: MemoryCommand,
) -> MemoryCommandResult:
    session = store.get_session(session_id)
    if session is None:
        return MemoryCommandResult(content="The Chat session no longer exists.", command=command.kind, mutated=False)
    privacy_rejected = _privacy_rejected(session, command)
    if privacy_rejected is not None:
        return privacy_rejected
    write_rejected = _write_rejected(session, command)
    if write_rejected is not None:
        return write_rejected
    read_rejected = _read_rejected(session, command)
    if read_rejected is not None:
        return read_rejected
    context = _scope(session)

    if command.kind == "save":
        record = service.create_explicit_memory(
            context,
            scope=command.scope,
            category=command.category,
            content=command.content,
            provenance_id=user_message_id,
        )
        return MemoryCommandResult(
            content=f"Saved as {record.scope} {record.category} memory. Refresh this Chat's memory to use it in the active conversation.",
            command="save",
            mutated=True,
            memory_ids=[record.id],
        )

    if command.kind == "list":
        records = service.list_active(context)
        if not records:
            return MemoryCommandResult(content="I do not have any approved memory available in this Chat's scope.", command="list", mutated=False)
        lines = [f"- [{record.scope}/{record.category}] {record.content}" for record in records]
        return MemoryCommandResult(
            content="Approved memory available to this Chat:\n" + "\n".join(lines),
            command="list",
            mutated=False,
            memory_ids=[record.id for record in records],
        )

    if command.kind == "forget":
        needle = " ".join(command.content.split()).casefold()
        matches = [record for record in service.list_active(context) if needle in record.normalized_content]
        if not matches:
            return MemoryCommandResult(content="No approved memory matched that description, so nothing was changed.", command="forget", mutated=False)
        if len(matches) > 1:
            choices = "\n".join(f"- {record.id}: {record.content}" for record in matches[:10])
            return MemoryCommandResult(content="More than one memory matched. Use `forget <unique text>` or the Memory view:\n" + choices, command="forget", mutated=False, memory_ids=[record.id for record in matches])
        record = matches[0]
        service.forget_memory(context, record.id, expected_revision=record.revision)
        return MemoryCommandResult(content="Forgot the matching memory and removed it from active snapshots.", command="forget", mutated=True, memory_ids=[record.id])

    if command.kind == "refresh":
        state = refresh_session_memory(
            store,
            service,
            session_id,
            RefreshSessionMemoryRequest(expected_snapshot_revision=session.memory_snapshot_revision),
        )
        count = state.memory_record_count if state else 0
        revision = state.snapshot_revision if state else None
        return MemoryCommandResult(content=f"Refreshed active Chat memory to snapshot revision {revision} with {count} records.", command="refresh", mutated=True)

    if command.kind == "disable":
        current = store.get_session(session_id)
        if current is None:
            return MemoryCommandResult(content="The Chat session no longer exists.", command="disable", mutated=False)
        if current.interaction_mode == "character":
            current.read_memory = False
            current.write_memory = False
            current.shared_memory_access = "none"
            current.memory_snapshot_id = None
            current.memory_snapshot_revision = None
            current.memory_record_count = 0
            current.memory_last_refreshed_at = None
        else:
            current.memory_enabled = False
        store._save_session(current)
        return MemoryCommandResult(content="Memory is disabled for this Chat. Saved records were not deleted.", command="disable", mutated=True)

    current_record = service.repository.get_record(command.memory_id or "")
    if current_record is None or current_record not in service.list_active(context):
        return MemoryCommandResult(content="That memory ID is not available in this Chat's scope.", command="update", mutated=False)
    updated = service.edit_memory(
        context,
        current_record.id,
        content=command.content,
        expected_revision=current_record.revision,
    )
    return MemoryCommandResult(content="Updated the memory. Refresh active Chat memory to use the revised text.", command="update", mutated=True, memory_ids=[updated.id])
