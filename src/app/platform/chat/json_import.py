"""Idempotent import of the legacy JSON Chat store into the provider-free repository."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ChatSession
from .repository import ChatImportState, InMemoryChatRepository


class LegacyChatImportError(RuntimeError):
    pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _validated_sessions(payload: dict[str, Any]) -> tuple[list[ChatSession], int, list[str]]:
    raw_sessions = payload.get("sessions", [])
    if not isinstance(raw_sessions, list):
        raise LegacyChatImportError("legacy Chat payload must contain a sessions list")

    sessions: list[ChatSession] = []
    errors: list[str] = []
    seen_sessions: set[str] = set()
    seen_messages: set[str] = set()
    skipped = 0
    for index, raw_session in enumerate(raw_sessions):
        try:
            session = ChatSession.model_validate(raw_session)
        except Exception as exc:
            skipped += 1
            errors.append(f"session[{index}] invalid: {type(exc).__name__}: {exc}")
            continue
        if session.id in seen_sessions:
            skipped += 1
            errors.append(f"session[{index}] duplicate session id: {session.id}")
            continue
        seen_sessions.add(session.id)

        unique_messages = []
        for message in session.messages:
            if message.id in seen_messages:
                errors.append(
                    f"session[{index}] duplicate message id skipped: {message.id}"
                )
                continue
            seen_messages.add(message.id)
            unique_messages.append(message)
        sessions.append(
            session.model_copy(
                update={
                    "messages": unique_messages,
                    "message_count": len(unique_messages),
                }
            )
        )
    return sessions, skipped, errors


def import_legacy_chat_json(
    repository: InMemoryChatRepository,
    source_path: str | Path,
) -> ChatImportState | None:
    path = Path(source_path)
    if not path.is_file():
        return None
    raw = path.read_bytes()
    digest = _source_hash(raw)
    existing = repository.get_import_state(str(path.resolve()))
    if existing and existing.source_hash == digest and existing.status == "completed":
        return existing

    try:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise LegacyChatImportError("legacy Chat payload root must be an object")
        sessions, skipped, errors = _validated_sessions(payload)
        return repository.import_sessions(
            source_path=str(path.resolve()),
            source_hash=digest,
            sessions=sessions,
            skipped_session_count=skipped,
            errors=errors,
            updated_at=_utcnow(),
        )
    except Exception as exc:
        repository.record_failed_import(
            source_path=str(path.resolve()),
            source_hash=digest,
            error=f"{type(exc).__name__}: {exc}",
            updated_at=_utcnow(),
        )
        if isinstance(exc, LegacyChatImportError):
            raise
        raise LegacyChatImportError(str(exc)) from exc
