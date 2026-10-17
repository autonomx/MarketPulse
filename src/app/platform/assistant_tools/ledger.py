"""Durable assistant tool execution ledger."""
from __future__ import annotations

from app.config.env import env_str, environment

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_LEDGER_PATH = Path("resources/data/assistant_tools_ledger.jsonl")


class AssistantToolLedgerEntry(BaseModel):
    execution_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str | None = None
    proposal_id: str | None = None
    tool_id: str
    action_id: str
    approval_source: str = "system"
    input_summary: str = ""
    result_summary: str = ""
    state_changed: bool = False
    error: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AssistantToolLedgerPayload(BaseModel):
    entries: list[AssistantToolLedgerEntry] = Field(default_factory=list)


def assistant_tool_ledger_path() -> Path:
    configured = environment().get("OMNIX_ASSISTANT_TOOLS_LEDGER_PATH")
    return Path(configured) if configured else DEFAULT_LEDGER_PATH


def summarize_tool_input(data: dict[str, object]) -> str:
    if not data:
        return "No input."
    parts = []
    for key in sorted(data)[:4]:
        value = data[key]
        text = str(value)
        if len(text) > 80:
            text = f"{text[:77]}..."
        parts.append(f"{key}={text}")
    suffix = "" if len(data) <= 4 else f" +{len(data) - 4} more"
    return ", ".join(parts) + suffix


def append_assistant_tool_ledger_entry(entry: AssistantToolLedgerEntry, path: Path | None = None) -> AssistantToolLedgerEntry:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.platform.assistant_tools.persistence.runtime_documents import append_assistant_tool_ledger_entry_postgres
        return append_assistant_tool_ledger_entry_postgres(entry, path)
    ledger_path = path or assistant_tool_ledger_path()
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(entry.model_dump_json() + "\n")
    return entry


def load_assistant_tool_ledger(path: Path | None = None, *, limit: int = 100) -> AssistantToolLedgerPayload:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.platform.assistant_tools.persistence.runtime_documents import load_assistant_tool_ledger_postgres
        return load_assistant_tool_ledger_postgres(path, limit=limit)
    ledger_path = path or assistant_tool_ledger_path()
    if not ledger_path.exists():
        return AssistantToolLedgerPayload()
    entries: list[AssistantToolLedgerEntry] = []
    try:
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return AssistantToolLedgerPayload()
    for line in lines[-max(1, limit):]:
        if not line.strip():
            continue
        try:
            entries.append(AssistantToolLedgerEntry.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError):
            continue
    entries.sort(key=lambda item: item.created_at, reverse=True)
    return AssistantToolLedgerPayload(entries=entries)


def assistant_tool_execution_for_proposal(proposal_id: str, path: Path | None = None) -> AssistantToolLedgerEntry | None:
    if not proposal_id:
        return None
    return next(
        (
            entry
            for entry in load_assistant_tool_ledger(path, limit=1000).entries
            if entry.proposal_id == proposal_id and entry.state_changed and not entry.error
        ),
        None,
    )
