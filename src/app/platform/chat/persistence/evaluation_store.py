"""PostgreSQL-backed live-chat evaluation persistence."""
from __future__ import annotations

from pathlib import Path

from app.platform.chat.evaluation_store import LiveChatEvaluationStore
from app.persistence.document_store import DocumentLock, PostgresDocumentStore
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.platform.chat.evaluation_store import VoiceSessionEvaluationRecord
from app.persistence.document_schemas import register_document_schema


class PostgresLiveChatEvaluationStore(LiveChatEvaluationStore):
    """Keep evaluation/policy validation logic while replacing JSON authority."""

    _lock: DocumentLock  # a cross-process document lock instead of the base's RLock

    def __init__(self, path: Path | None = None) -> None:
        if path is not None:
            raise RuntimeError(
                "file-backed live-chat evaluation authority is retired; use the legacy importer"
            )
        self.path = Path("postgresql://live-chat-evaluations")
        self._documents = PostgresDocumentStore()
        # Serializes read-modify-write across processes (WP-5.9).
        self._lock = self._documents.lock(module="live-chat", record_type="evaluation-policy-store")

    def _read(self) -> dict:
        payload = (
            self._lock.read(default=None)
            if self._lock.held
            else self._documents.read(module="live-chat", record_type="evaluation-policy-store", default=None)
        )
        if not isinstance(payload, dict):
            return self._fresh_payload()
        payload = dict(payload)
        payload.setdefault("format_version", 2)
        payload.setdefault("evaluations", [])
        policies = payload.setdefault("presence_policies", {})
        self._ensure_default_policies(policies)
        return payload

    def _write(self, payload: dict) -> None:
        if self._lock.held:
            self._lock.write(payload)
            return
        self._documents.write(payload, module="live-chat", record_type="evaluation-policy-store")


class LiveChatEvaluationDocument(BaseModel):
    """The ``live-chat/evaluation-policy-store`` document."""

    model_config = ConfigDict(extra="allow")

    format_version: int = 2
    evaluations: list[VoiceSessionEvaluationRecord] = Field(default_factory=list)
    presence_policies: dict[str, Any] = Field(default_factory=dict)


# Document shapes (WP-5.9).
register_document_schema("live-chat", "evaluation-policy-store", LiveChatEvaluationDocument)
