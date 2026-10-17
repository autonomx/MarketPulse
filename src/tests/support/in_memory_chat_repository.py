"""Provider-free in-memory test double for chat persistence."""

from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from app.platform.chat.models import ChatSession
from app.platform.chat.repository_models import ChatImportState


@dataclass
class _State:
    lock: threading.RLock = field(default_factory=threading.RLock)
    sessions: list[ChatSession] = field(default_factory=list)
    imports: dict[str, ChatImportState] = field(default_factory=dict)


_STATES: dict[str, _State] = {}
_STATES_LOCK = threading.RLock()


def _state(path: str | Path | None) -> _State:
    key = str(path or ":memory:chat")
    with _STATES_LOCK:
        return _STATES.setdefault(key, _State())


class InMemoryChatRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else Path(":memory:")
        self._state = _state(db_path)

    def load_sessions(self) -> list[ChatSession]:
        with self._state.lock:
            return deepcopy(self._state.sessions)

    def save_sessions(self, sessions: list[ChatSession]) -> None:
        with self._state.lock:
            self._state.sessions = deepcopy(sessions)

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
            existing = self._state.imports.get(source_path)
            if existing and existing.source_hash == source_hash and existing.status == "completed":
                return deepcopy(existing)
            existing_by_id = {session.id: session for session in self._state.sessions}
            for session in sessions:
                existing_by_id[session.id] = deepcopy(session)
            self._state.sessions = sorted(
                existing_by_id.values(),
                key=lambda item: (item.created_at, item.id),
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
            self._state.imports[source_path] = state
            return deepcopy(state)

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
            self._state.imports[source_path] = state
        return deepcopy(state)

    def get_import_state(self, source_path: str) -> ChatImportState | None:
        with self._state.lock:
            state = self._state.imports.get(source_path)
            return deepcopy(state) if state is not None else None

    def counts(self) -> tuple[int, int]:
        with self._state.lock:
            return (
                len(self._state.sessions),
                sum(len(session.messages) for session in self._state.sessions),
            )


def sessions_for_path(path: str | Path | None) -> list[ChatSession]:
    return InMemoryChatRepository(path).load_sessions()


def reset_in_memory_chat_repositories() -> None:
    with _STATES_LOCK:
        _STATES.clear()
