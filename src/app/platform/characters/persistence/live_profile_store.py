"""PostgreSQL-backed Live Conversation profiles."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.platform.characters.live_conversation_profile import (
    LiveConversationProfile,
    LiveConversationProfileEnvelope,
    LiveConversationProfileStore,
)
from app.persistence.document_store import PostgresDocumentStore
from app.persistence.document_schemas import register_document_schema


class PostgresLiveConversationProfileStore(LiveConversationProfileStore):
    def __init__(self, path: Path | None = None) -> None:
        if path is not None:
            raise RuntimeError("file-backed live-conversation profiles are retired")
        self.path = Path("postgresql:/live-conversation-profiles")
        self._documents = PostgresDocumentStore()
        # Serializes read-modify-write across processes (WP-5.9).
        self._lock = self._documents.lock(module="live-chat", record_type="conversation-profiles")

    def _read(self) -> dict[str, Any]:
        payload = (
            self._lock.read(default=None)
            if self._lock.held
            else self._documents.read(module="live-chat", record_type="conversation-profiles", default=None)
        )
        if not isinstance(payload, dict):
            return {"format_version": 1, "defaults": {}, "sessions": {}}
        result = dict(payload)
        result.setdefault("format_version", 1)
        result.setdefault("defaults", {})
        result.setdefault("sessions", {})
        return result

    def _write(self, payload: dict[str, Any]) -> None:
        if self._lock.held:
            self._lock.write(dict(payload))
            return
        self._documents.write(dict(payload), module="live-chat", record_type="conversation-profiles")


class LiveConversationProfilesDocument(BaseModel):
    """The ``live-chat/conversation-profiles`` document: defaults and per-session profiles."""

    model_config = ConfigDict(extra="allow")

    format_version: int = 1
    defaults: LiveConversationProfile | dict[str, Any] = Field(default_factory=dict)
    sessions: dict[str, LiveConversationProfile] = Field(default_factory=dict)


# Document shapes (WP-5.9).
register_document_schema("live-chat", "conversation-profiles", LiveConversationProfilesDocument)
