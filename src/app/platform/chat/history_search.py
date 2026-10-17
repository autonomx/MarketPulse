"""Scope-first provider-free retrieval for historical chat messages.

PostgreSQL full-text/search queries are installed for production. Tests use the
in-memory chat repository and deterministic token matching.
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .memory_port import memory_runtime_settings as load_memory_runtime_settings

from .models import ChatMessage, MessageContentPurpose, project_message_content
from .prompt_assembly import PromptHistoryItem
from .repository import InMemoryChatRepository
from app.platform.chat.models import ChatSession
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    pass

_TERM_PATTERN = re.compile(r"[A-Za-z0-9_]{2,}")


class HistorySearchStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    available: bool
    reason: str
    indexed_messages: int = Field(default=0, ge=0)


class HistorySearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[PromptHistoryItem] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)
    status: HistorySearchStatus


_LOW_INFORMATION_TERMS = {
    "a", "an", "and", "apply", "change", "do", "fix", "go", "implement",
    "it", "make", "one", "that", "the", "them", "this", "those", "update",
    "yes", "issue", "problem", "thing", "option", "suggestion",
}


def history_query_is_low_information(query: str) -> bool:
    latest = " ".join(str(query or "").split())
    terms = [term.casefold() for term in _TERM_PATTERN.findall(latest)]
    meaningful = [term for term in terms if term not in _LOW_INFORMATION_TERMS]
    return bool(terms) and (
        len(meaningful) <= 2
        or (
            any(term in _LOW_INFORMATION_TERMS for term in terms)
            and len(terms) <= 6
        )
    )


def build_history_recall_query(
    query: str,
    *,
    recent_messages: list[ChatMessage] | None = None,
    session_summary: str | None = None,
) -> str:
    """Expand low-information references before cross-session retrieval.

    History search remains provider-free and scope-first. Expansion only adds
    bounded clues already present in the current session/summary; it never adds
    authority or broadens workspace/profile scope.
    """

    latest = " ".join(str(query or "").split())
    if not history_query_is_low_information(latest):
        return latest

    clues: list[str] = []
    summary = " ".join(str(session_summary or "").split())
    if summary:
        clues.append(summary[:900])
    for message in reversed(list(recent_messages or [])):
        if message.role not in {"user", "assistant"}:
            continue
        content = " ".join(
            project_message_content(message, MessageContentPurpose.SEARCH).split()
        )
        if not content or content == latest:
            continue
        clues.append(content[:320])
        if len(clues) >= 7:
            break
    clues.reverse()
    if not clues:
        return latest
    return latest + "\n" + "\n".join(clues)


def history_recall_enabled() -> bool:
    return load_memory_runtime_settings().history_recall_enabled


class HistorySearchService(Protocol):
    """What a chat store needs from history search (in memory or PostgreSQL)."""

    def ensure_index(self) -> HistorySearchStatus: ...

    def sync_index(self) -> HistorySearchStatus: ...

    def search(
        self,
        query: str,
        *,
        profile_id: str,
        workspace_id: str,
        project_id: str | None,
        exclude_session_id: str | None = None,
        limit: int = 6,
    ) -> HistorySearchResult: ...


class InMemoryHistorySearchService:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else None

    @staticmethod
    def _status_for_sessions(sessions: list[ChatSession]) -> HistorySearchStatus:
        count = sum(
            1
            for session in sessions
            for message in session.messages
            if message.role in {"user", "assistant"}
            and project_message_content(message, MessageContentPurpose.SEARCH).strip()
        )
        return HistorySearchStatus(
            available=True,
            reason="in_memory_ready",
            indexed_messages=count,
        )

    def ensure_index(self) -> HistorySearchStatus:
        return self._status_for_sessions(
            InMemoryChatRepository(self.db_path).load_sessions()
        )

    def sync_index(self) -> HistorySearchStatus:
        return self.ensure_index()

    def search_sessions(
        self,
        sessions: list[ChatSession],
        query: str,
        *,
        profile_id: str,
        workspace_id: str,
        project_id: str | None,
        exclude_session_id: str | None = None,
        limit: int = 6,
    ) -> HistorySearchResult:
        """Search an explicit canonical Chat snapshot, independent of store backend."""

        original_query = str(query or "").splitlines()[0].strip()
        low_information_query = history_query_is_low_information(original_query)
        terms = list(
            dict.fromkeys(term.casefold() for term in _TERM_PATTERN.findall(query))
        )[:12]
        status = self._status_for_sessions(sessions)
        if not terms:
            return HistorySearchResult(items=[], query_terms=terms, status=status)

        matches: list[tuple[int, str, PromptHistoryItem]] = []
        scoped_recent: list[tuple[str, PromptHistoryItem]] = []
        for session in sessions:
            if session.profile_id != profile_id or session.workspace_id != workspace_id:
                continue
            if (session.project_id or "") != (project_id or ""):
                continue
            if exclude_session_id and session.id == exclude_session_id:
                continue
            for message in session.messages:
                if message.role not in {"user", "assistant"}:
                    continue
                content = project_message_content(
                    message,
                    MessageContentPurpose.SEARCH,
                )
                item = PromptHistoryItem(
                    session_id=session.id,
                    message_id=message.id,
                    role=message.role,
                    content=content,
                    created_at=message.created_at,
                )
                scoped_recent.append((message.created_at, item))
                lowered = content.casefold()
                score = sum(1 for term in terms if term in lowered)
                if score == 0:
                    continue
                matches.append((score, message.created_at, item))

        matches.sort(
            key=lambda item: (item[0], item[1], item[2].message_id),
            reverse=True,
        )
        bounded_limit = max(0, min(int(limit), 50))
        items = [item[2] for item in matches[:bounded_limit]]
        if not items and low_information_query:
            scoped_recent.sort(
                key=lambda item: (item[0], item[1].message_id),
                reverse=True,
            )
            items = [item[1] for item in scoped_recent[:bounded_limit]]
        return HistorySearchResult(
            items=items,
            query_terms=terms,
            status=status,
        )

    def search(
        self,
        query: str,
        *,
        profile_id: str,
        workspace_id: str,
        project_id: str | None,
        exclude_session_id: str | None = None,
        limit: int = 6,
    ) -> HistorySearchResult:
        return self.search_sessions(
            InMemoryChatRepository(self.db_path).load_sessions(),
            query,
            profile_id=profile_id,
            workspace_id=workspace_id,
            project_id=project_id,
            exclude_session_id=exclude_session_id,
            limit=limit,
        )


def default_history_search_service() -> HistorySearchService:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.platform.chat.persistence.chat_runtime import default_history_search_service as factory
        return factory()
    return InMemoryHistorySearchService()
