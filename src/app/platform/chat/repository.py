"""Repository abstraction and in-memory implementation for Chat history."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .models import ChatSession


class ChatRepository(Protocol):
    def load_sessions(self) -> list[ChatSession]: ...

    def save_session(self, session: ChatSession) -> None: ...


class ChatImportState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    source_hash: str
    status: str
    imported_session_count: int = Field(ge=0)
    imported_message_count: int = Field(ge=0)
    skipped_session_count: int = Field(ge=0)
    errors: list[str] = Field(default_factory=list)
    updated_at: str


@dataclass
class _State:
    lock: RLock = field(default_factory=RLock)
    sessions: list[ChatSession] = field(default_factory=list)
    imports: dict[str, ChatImportState] = field(default_factory=dict)


_STATES: dict[str, _State] = {}
_STATES_LOCK = RLock()


def _state(path: str | Path | None) -> _State:
    key = str(Path(path)) if path is not None else ":memory:chat"
    with _STATES_LOCK:
        return _STATES.setdefault(key, _State())


class InMemoryChatRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else None
        self._state = _state(self.db_path)

    def load_sessions(self) -> list[ChatSession]:
        with self._state.lock:
            return deepcopy(self._state.sessions)

    def save_session(self, session: ChatSession) -> None:
        with self._state.lock:
            sessions = {value.id: value for value in self._state.sessions}
            sessions[session.id] = deepcopy(session)
            self._state.sessions = sorted(
                sessions.values(), key=lambda value: (value.created_at, value.id)
            )

    def import_sessions(
        self,
        *,
        source_path: str,
        source_hash: str,
        sessions: list[ChatSession],
        skipped_session_count: int,
        errors: list[str],
        updated_at: str,
    ) -> ChatImportState:
        with self._state.lock:
            current = self._state.imports.get(source_path)
            if current is not None and current.source_hash == source_hash and current.status == "completed":
                return deepcopy(current)
            existing = {session.id: session for session in self._state.sessions}
            existing.update({session.id: deepcopy(session) for session in sessions})
            self._state.sessions = sorted(
                existing.values(),
                key=lambda session: (session.created_at, session.id),
            )
            state = ChatImportState(
                source_path=source_path,
                source_hash=source_hash,
                status="completed",
                imported_session_count=len(sessions),
                imported_message_count=sum(len(session.messages) for session in sessions),
                skipped_session_count=skipped_session_count,
                errors=list(errors),
                updated_at=updated_at,
            )
            self._state.imports[source_path] = deepcopy(state)
            return state

    def record_failed_import(
        self,
        *,
        source_path: str,
        source_hash: str,
        error: str,
        updated_at: str,
    ) -> ChatImportState:
        state = ChatImportState(
            source_path=source_path,
            source_hash=source_hash,
            status="failed",
            imported_session_count=0,
            imported_message_count=0,
            skipped_session_count=0,
            errors=[error],
            updated_at=updated_at,
        )
        with self._state.lock:
            self._state.imports[source_path] = deepcopy(state)
        return state

    def get_import_state(self, source_path: str) -> ChatImportState | None:
        with self._state.lock:
            value = self._state.imports.get(source_path)
            return deepcopy(value) if value is not None else None

    def counts(self) -> tuple[int, int]:
        with self._state.lock:
            return len(self._state.sessions), sum(len(session.messages) for session in self._state.sessions)
