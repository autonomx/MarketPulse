"""PostgreSQL assistant-turn coordinator."""
from __future__ import annotations

import threading
from pathlib import Path
from pydantic import ValidationError

from app.platform.chat.assistant_turns import AssistantTurnCoordinator, AssistantTurnRecord
from app.persistence.document_store import PostgresDocumentStore
from app.persistence.document_schemas import register_document_schema
from app.persistence.transaction_binding import after_commit


class PostgresAssistantTurnCoordinator(AssistantTurnCoordinator):
    def __init__(self, path: str | Path | None = None, *, database=None) -> None:
        if path is not None:
            raise RuntimeError("file-backed assistant-turn authority is retired")
        self._record_type = AssistantTurnRecord
        self.path = Path("postgresql:/assistant-turns")
        self._lock = threading.RLock()
        self._documents = (
            PostgresDocumentStore(database=database)
            if database is not None
            else PostgresDocumentStore()
        )
        self._records = self._load()
        self._persisted_records = {
            key: record.model_dump(mode="json")
            for key, record in self._records.items()
        }

    def _load(self) -> dict[str, AssistantTurnRecord]:
        payload = self._documents.read(
            module="chat",
            record_type="assistant-turns",
            default=[],
        )
        records: dict[str, AssistantTurnRecord] = {}
        for item in payload if isinstance(payload, list) else []:
            try:
                record = AssistantTurnRecord.model_validate(item)
            except ValidationError:
                continue
            records[record.assistant_turn_id] = record
        for _, item, _ in self._documents.list(
            module="chat",
            record_type="assistant-turn",
            limit=5000,
        ):
            try:
                record = AssistantTurnRecord.model_validate(item)
            except ValidationError:
                continue
            records[record.assistant_turn_id] = record
        return records

    def _save(self) -> None:
        changed = {
            key: record.model_dump(mode="json")
            for key, record in self._records.items()
            if self._persisted_records.get(key) != record.model_dump(mode="json")
        }
        for key, payload in changed.items():
            self._documents.write(
                payload,
                module="chat",
                record_type="assistant-turn",
                record_id=key,
            )

        def remember() -> None:
            with self._lock:
                self._persisted_records.update(changed)

        after_commit(self._documents.database, remember)


# Document shapes (WP-5.9); ``assistant-turns`` is the retired single-list form, read only.
register_document_schema("chat", "assistant-turn", AssistantTurnRecord)
register_document_schema("chat", "assistant-turns", list[AssistantTurnRecord])
