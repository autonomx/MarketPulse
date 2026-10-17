"""Legacy chat-session compatibility over PostgreSQL documents."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.persistence.document_store import PostgresDocumentStore
from app.persistence.document_schemas import register_document_schema
from app.providers.service import get_global_system_prompt
from app.runtime.pagination import MAX_PAGE_SIZE

_MODULE = "platform"
_RECORD_TYPE = "legacy-session"


class LegacySessionListItem(BaseModel):
    id: str
    title: str = "New Chat"
    updated_at: str = ""


class LegacySessionListResponse(BaseModel):
    success: bool = True
    sessions: list[LegacySessionListItem] = Field(default_factory=list)


class LegacySessionCreateResponse(BaseModel):
    success: bool = True
    session_id: str


class LegacySessionResponse(BaseModel):
    success: bool = True
    session: dict[str, Any]


class LegacySuccessResponse(BaseModel):
    success: bool = True


class LegacySessionUpdateRequest(BaseModel):
    title: str | None = None
    system_prompt: str | None = None


class LegacyGenerateTitleRequest(BaseModel):
    user_message: str = ""
    ai_response: str = ""


class LegacyGenerateTitleResponse(BaseModel):
    success: bool = True
    title: str


def _store() -> PostgresDocumentStore:
    return PostgresDocumentStore()


def list_legacy_sessions() -> LegacySessionListResponse:
    items: list[LegacySessionListItem] = []
    for session_id, payload, _revision in _store().list(
        module=_MODULE, record_type=_RECORD_TYPE, limit=MAX_PAGE_SIZE
    ):
        if not isinstance(payload, dict):
            continue
        items.append(
            LegacySessionListItem(
                id=session_id,
                title=str(payload.get("title") or "New Chat"),
                updated_at=str(payload.get("updated_at") or ""),
            )
        )
    items.sort(key=lambda item: item.updated_at, reverse=True)
    return LegacySessionListResponse(sessions=items)


def create_legacy_session() -> LegacySessionCreateResponse:
    session_id = str(uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    _store().write(
        {
            "title": "New Chat",
            "messages": [],
            "system_prompt": get_global_system_prompt(),
            "created_at": now,
            "updated_at": now,
        },
        module=_MODULE,
        record_type=_RECORD_TYPE,
        record_id=session_id,
    )
    return LegacySessionCreateResponse(session_id=session_id)


def get_legacy_session(session_id: str) -> LegacySessionResponse | None:
    session = _store().read(
        module=_MODULE,
        record_type=_RECORD_TYPE,
        record_id=session_id,
        default=None,
    )
    return LegacySessionResponse(session=session) if isinstance(session, dict) else None


def update_legacy_session(
    session_id: str, request: LegacySessionUpdateRequest
) -> LegacySuccessResponse | None:
    store = _store()
    session, revision = store.read_versioned(
        module=_MODULE,
        record_type=_RECORD_TYPE,
        record_id=session_id,
        default=None,
    )
    if not isinstance(session, dict):
        return None
    if request.title is not None:
        session["title"] = request.title
    if request.system_prompt is not None:
        session["system_prompt"] = request.system_prompt
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Conditional: a concurrent edit raises DocumentRevisionConflict (409).
    store.write(session, module=_MODULE, record_type=_RECORD_TYPE, record_id=session_id, expected_revision=revision)
    return LegacySuccessResponse()


def delete_legacy_session(session_id: str) -> LegacySuccessResponse | None:
    deleted = _store().delete(
        module=_MODULE, record_type=_RECORD_TYPE, record_id=session_id
    )
    return LegacySuccessResponse() if deleted else None


def generate_legacy_session_title(
    request: LegacyGenerateTitleRequest,
) -> LegacyGenerateTitleResponse:
    first_line = request.user_message.split("\n")[0].strip() if request.user_message else ""
    if not first_line:
        first_line = request.ai_response.split("\n")[0].strip() if request.ai_response else ""
    return LegacyGenerateTitleResponse(title=(first_line[:50] if first_line else "New Chat"))


class LegacySessionDocument(BaseModel):
    """The ``platform/legacy-session`` document."""

    title: str = "New Chat"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    system_prompt: str | None = None
    created_at: str = ""
    updated_at: str = ""


# Document shapes (WP-5.9).
register_document_schema(_MODULE, _RECORD_TYPE, LegacySessionDocument)
