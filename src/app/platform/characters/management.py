"""Explicit export and destructive lifecycle operations for Character Mode."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.runtime.pagination import MAX_PAGE_SIZE

from .models import CharacterProfile, CharacterProfileVersion
from .service import CharacterService


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()



def _all_pages(list_page: Any, **filters: Any) -> list[Any]:
    items: list[Any] = []
    while True:
        page = list_page(limit=MAX_PAGE_SIZE, offset=len(items), **filters)
        items.extend(page)
        if len(page) < MAX_PAGE_SIZE:
            return items

class CharacterSessionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    message_count: int = Field(ge=0)
    character_message_count: int = Field(ge=0)
    created_at: str
    updated_at: str


class CharacterDataExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character: CharacterProfile
    versions: list[CharacterProfileVersion]
    memories: list[dict[str, Any]]
    pending_suggestions: list[dict[str, Any]]
    sessions: list[CharacterSessionSummary]
    generated_at: str


class CharacterDataActionRequest(BaseModel):
    """All destructive choices are independent and require typed confirmation."""

    model_config = ConfigDict(extra="forbid")

    confirm_character_id: str = Field(min_length=1, max_length=160)
    delete_memories: bool = False
    delete_transcripts: bool = False
    unlink_voice: bool = False
    archive_profile: bool = False

    @model_validator(mode="after")
    def require_action(self) -> "CharacterDataActionRequest":
        if not any(
            (
                self.delete_memories,
                self.delete_transcripts,
                self.unlink_voice,
                self.archive_profile,
            )
        ):
            raise ValueError("at least one character data action is required")
        return self


class CharacterDataActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    character_id: str
    deleted_memory_records: int = Field(ge=0)
    deleted_memory_candidates: int = Field(ge=0)
    deleted_memory_snapshots: int = Field(ge=0)
    deleted_transcript_messages: int = Field(ge=0)
    voice_unlinked: bool = False
    profile_archived: bool = False


class CharacterManagementService:
    def __init__(
        self,
        character_service: CharacterService,
        chat_store: Any,
        memory_repository: Any,
    ) -> None:
        self.character_service = character_service
        self.chat_store = chat_store
        self.memory_repository = memory_repository

    def export(self, character_id: str) -> CharacterDataExport:
        profile = self.character_service.get(character_id, include_archived=True)
        # Every memory and candidate of the character, page by page (WP-5.5).
        records = _all_pages(
            self.memory_repository.list_records,
            owner_type="character",
            owner_id=character_id,
            status=None,
        )
        candidates = []
        for status in ("pending", "accepted", "rejected"):
            candidates.extend(
                _all_pages(
                    self.memory_repository.list_candidates,
                    owner_type="character",
                    owner_id=character_id,
                    status=status,
                )
            )
        sessions = self._session_summaries(character_id)
        return CharacterDataExport(
            character=profile,
            versions=self.character_service.versions(character_id).versions,
            memories=[record.model_dump(mode="json") for record in records],
            pending_suggestions=[
                candidate.model_dump(mode="json")
                for candidate in candidates
                if candidate.status == "pending"
            ],
            sessions=sessions,
            generated_at=_utcnow(),
        )

    def apply(
        self,
        character_id: str,
        request: CharacterDataActionRequest,
    ) -> CharacterDataActionResponse:
        if request.confirm_character_id != character_id:
            raise ValueError("character confirmation does not match")
        profile = self.character_service.get(character_id, include_archived=True)
        memory_counts = (0, 0, 0)
        transcript_count = 0
        voice_unlinked = False
        profile_archived = False

        if request.delete_memories:
            memory_counts = self._delete_memory_owner(character_id)
        if request.delete_transcripts:
            transcript_count = self._delete_character_transcripts(character_id)
        if request.unlink_voice and profile.default_voice_asset_id:
            profile = self.character_service.update(
                character_id,
                __import__(
                    "app.platform.characters.models",
                    fromlist=["UpdateCharacterRequest"],
                ).UpdateCharacterRequest(
                    expected_version=profile.active_version,
                    clear_default_voice=True,
                ),
            )
            voice_unlinked = True
        if request.archive_profile and profile.status != "archived":
            self.character_service.archive(character_id)
            profile_archived = True

        return CharacterDataActionResponse(
            character_id=character_id,
            deleted_memory_records=memory_counts[0],
            deleted_memory_candidates=memory_counts[1],
            deleted_memory_snapshots=memory_counts[2],
            deleted_transcript_messages=transcript_count,
            voice_unlinked=voice_unlinked,
            profile_archived=profile_archived,
        )

    def _session_summaries(self, character_id: str) -> list[CharacterSessionSummary]:
        summaries: list[CharacterSessionSummary] = []
        cursor = None
        while True:
            page = self.chat_store.list_sessions(limit=100, cursor=cursor)
            for summary in page.sessions:
                session = self.chat_store.get_session(summary.id)
                if session is None:
                    continue
                segment_ids = {
                    segment.id
                    for segment in self.chat_store.segments(session.id)
                    if segment.character_id == character_id
                }
                character_messages = sum(
                    1
                    for message in session.messages
                    if message.metadata.get("segment_id") in segment_ids
                    or message.metadata.get("character_id") == character_id
                )
                if session.character_id != character_id and character_messages == 0:
                    continue
                summaries.append(
                    CharacterSessionSummary(
                        id=session.id,
                        title=session.title,
                        message_count=len(session.messages),
                        character_message_count=character_messages,
                        created_at=session.created_at,
                        updated_at=session.updated_at,
                    )
                )
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        return summaries

    def _delete_memory_owner(self, character_id: str) -> tuple[int, int, int]:
        return self.memory_repository.delete_owner(
            owner_type="character",
            owner_id=character_id,
        )

    def _delete_character_transcripts(self, character_id: str) -> int:
        deleted = 0
        cursor = None
        while True:
            page = self.chat_store.list_sessions(limit=100, cursor=cursor)
            for summary in page.sessions:
                session = self.chat_store.get_session(summary.id)
                if session is None:
                    continue
                segment_ids = {
                    segment.id
                    for segment in self.chat_store.segments(session.id)
                    if segment.character_id == character_id
                }
                message_ids = [
                    message.id
                    for message in session.messages
                    if message.metadata.get("segment_id") in segment_ids
                    or message.metadata.get("character_id") == character_id
                ]
                delete_messages = getattr(self.chat_store, "delete_messages", None)
                if callable(delete_messages):
                    deleted += delete_messages(session.id, message_ids)
                elif message_ids:
                    delete_ids = set(message_ids)
                    session.messages = [
                        message for message in session.messages if message.id not in delete_ids
                    ]
                    session.message_count = len(session.messages)
                    self.chat_store._save_session(session)
                    deleted += len(message_ids)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        return deleted


__all__ = [
    "CharacterDataActionRequest",
    "CharacterDataActionResponse",
    "CharacterDataExport",
    "CharacterManagementService",
    "CharacterSessionSummary",
]
