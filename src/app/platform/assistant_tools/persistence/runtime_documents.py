"""PostgreSQL document adapters owned by Assistant Tools."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.persistence.document_store import PostgresDocumentStore
from app.persistence.document_schemas import register_document_schema


def append_assistant_tool_ledger_entry_postgres(entry: Any, path: Path | None = None) -> Any:
    if path is not None:
        raise RuntimeError("file-backed assistant-tool ledger authority is retired")
    PostgresDocumentStore().write(
        entry.model_dump(mode="json"),
        module="assistant-tools",
        record_type="execution-ledger",
        record_id=str(entry.execution_id),
    )
    return entry


def load_assistant_tool_ledger_postgres(
    path: Path | None = None,
    *,
    limit: int = 100,
):
    if path is not None:
        raise RuntimeError("file-backed assistant-tool ledger authority is retired")
    from app.platform.assistant_tools.ledger import AssistantToolLedgerEntry, AssistantToolLedgerPayload

    entries = []
    for _, payload, _ in PostgresDocumentStore().list(
        module="assistant-tools",
        record_type="execution-ledger",
        limit=max(1, int(limit)),
    ):
        try:
            entries.append(AssistantToolLedgerEntry.model_validate(payload))
        except (TypeError, ValueError):
            continue
    entries.sort(key=lambda item: item.created_at, reverse=True)
    return AssistantToolLedgerPayload(entries=entries[: max(1, int(limit))])


def _register_document_schemas() -> None:
    from app.platform.assistant_tools.ledger import AssistantToolLedgerEntry

    register_document_schema("assistant-tools", "execution-ledger", AssistantToolLedgerEntry)


# Document shapes (WP-5.9).
_register_document_schemas()
