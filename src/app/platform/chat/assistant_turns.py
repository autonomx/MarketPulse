"""Authoritative lifecycle coordination for streamed assistant turns."""
from __future__ import annotations

import logging

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.config.env import env_str

from app.runtime.paths import resources_data_root

logger = logging.getLogger(__name__)

AssistantLifecycle = Literal[
    "created",
    "streaming",
    "stopping",
    "completed",
    "interrupted",
    "failed",
]
ProviderExecution = Literal[
    "not_started",
    "running",
    "cancel_requested",
    "cancelled",
    "completed",
    "uninterruptible",
]
DeliveryState = Literal["none", "partial", "full"]
ToolExecution = Literal[
    "none",
    "running",
    "cancel_requested",
    "cancelled",
    "committed",
    "failed",
]
DeliveryPolicy = Literal["audio_only", "visual_or_audio", "reveal_as_spoken"]

_TERMINAL_LIFECYCLES = {"completed", "interrupted", "failed"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_assistant_turn_store_path() -> Path:
    override = env_str("OMNIX_ASSISTANT_TURN_STORE_PATH", "")
    return Path(override) if override else resources_data_root() / "assistant_turns.json"


class AssistantTurnRecord(BaseModel):
    assistant_turn_id: str
    session_id: str
    user_message_id: str
    user_turn_id: str
    speech_segment_id: str | None = None
    lifecycle: AssistantLifecycle = "created"
    provider_execution: ProviderExecution = "not_started"
    delivery: DeliveryState = "none"
    tool_execution: ToolExecution = "none"
    terminal_version: int = Field(default=0, ge=0)
    cancellation_reason: str | None = None
    generated_phrase_count: int = Field(default=0, ge=0)
    audio_delivered_phrase_count: int = Field(default=0, ge=0)
    audio_interrupted_phrase_index: int | None = Field(default=None, ge=0)
    audio_played_samples: int = Field(default=0, ge=0)
    visual_delivered_text_end: int = Field(default=0, ge=0)
    context_delivered_text_end: int = Field(default=0, ge=0)
    delivery_policy: DeliveryPolicy = "reveal_as_spoken"
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)

    @property
    def terminal(self) -> bool:
        return self.lifecycle in _TERMINAL_LIFECYCLES


class AssistantTurnCoordinator:
    """Thread-safe, durable logical cancellation and delivery boundary."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_assistant_turn_store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._records = self._load()

    def start(
        self,
        *,
        session_id: str,
        user_message_id: str,
        user_turn_id: str,
        speech_segment_id: str | None = None,
    ) -> AssistantTurnRecord:
        with self._lock:
            existing = self.find_by_user_turn(session_id, user_turn_id)
            if existing is not None:
                return existing
            record = AssistantTurnRecord(
                assistant_turn_id=f"assistant-turn:{uuid.uuid4().hex}",
                session_id=session_id,
                user_message_id=user_message_id,
                user_turn_id=user_turn_id,
                speech_segment_id=speech_segment_id,
            )
            self._records[record.assistant_turn_id] = record
            self._save()
            return record.model_copy(deep=True)

    def start_streaming(
        self,
        *,
        session_id: str,
        user_message_id: str,
        user_turn_id: str,
        speech_segment_id: str | None = None,
    ) -> AssistantTurnRecord:
        """Persist a directly streamed turn as running in its initial write."""
        with self._lock:
            existing = self.find_by_user_turn(session_id, user_turn_id)
            if existing is not None:
                record = self._records[existing.assistant_turn_id]
                if record.terminal:
                    return record.model_copy(deep=True)
                if (
                    record.lifecycle != "streaming"
                    or record.provider_execution != "running"
                ):
                    record.lifecycle = "streaming"
                    record.provider_execution = "running"
                    record.updated_at = _utcnow()
                    self._save()
                return record.model_copy(deep=True)
            record = AssistantTurnRecord(
                assistant_turn_id=f"assistant-turn:{uuid.uuid4().hex}",
                session_id=session_id,
                user_message_id=user_message_id,
                user_turn_id=user_turn_id,
                speech_segment_id=speech_segment_id,
                lifecycle="streaming",
                provider_execution="running",
            )
            self._records[record.assistant_turn_id] = record
            self._save()
            return record.model_copy(deep=True)

    def get(self, assistant_turn_id: str) -> AssistantTurnRecord | None:
        with self._lock:
            record = self._records.get(assistant_turn_id)
            return record.model_copy(deep=True) if record is not None else None

    def find_by_user_turn(self, session_id: str, user_turn_id: str) -> AssistantTurnRecord | None:
        with self._lock:
            for record in self._records.values():
                if record.session_id == session_id and record.user_turn_id == user_turn_id:
                    return record.model_copy(deep=True)
            return None

    def mark_streaming(self, assistant_turn_id: str) -> AssistantTurnRecord | None:
        return self._transition(
            assistant_turn_id,
            lifecycle="streaming",
            provider_execution="running",
            allow_terminal=False,
        )

    def record_delivery(
        self,
        assistant_turn_id: str,
        *,
        generated_phrase_count: int,
        audio_delivered_phrase_count: int,
        audio_interrupted_phrase_index: int | None,
        audio_played_samples: int,
        visual_delivered_text_end: int,
        context_delivered_text_end: int,
        delivery_policy: DeliveryPolicy = "reveal_as_spoken",
    ) -> AssistantTurnRecord | None:
        with self._lock:
            record = self._records.get(assistant_turn_id)
            if record is None:
                return None
            record.generated_phrase_count = max(record.generated_phrase_count, generated_phrase_count)
            record.audio_delivered_phrase_count = max(
                record.audio_delivered_phrase_count,
                min(audio_delivered_phrase_count, record.generated_phrase_count),
            )
            if audio_interrupted_phrase_index is not None:
                record.audio_interrupted_phrase_index = max(0, audio_interrupted_phrase_index)
            record.audio_played_samples = max(record.audio_played_samples, audio_played_samples)
            record.visual_delivered_text_end = max(
                record.visual_delivered_text_end,
                visual_delivered_text_end,
            )
            record.context_delivered_text_end = max(
                record.context_delivered_text_end,
                min(context_delivered_text_end, record.visual_delivered_text_end),
            )
            record.delivery_policy = delivery_policy
            if record.context_delivered_text_end > 0:
                record.delivery = "partial" if not record.terminal else record.delivery
            record.updated_at = _utcnow()
            self._save()
            return record.model_copy(deep=True)

    def request_cancel(self, assistant_turn_id: str, reason: str) -> AssistantTurnRecord | None:
        """Atomically make a confirmed interruption authoritative and idempotent."""
        with self._lock:
            record = self._records.get(assistant_turn_id)
            if record is None:
                return None
            if record.lifecycle == "interrupted":
                return record.model_copy(deep=True)
            if record.lifecycle in {"completed", "failed"}:
                return record.model_copy(deep=True)
            record.lifecycle = "interrupted"
            record.provider_execution = (
                "cancelled" if record.provider_execution == "not_started" else "cancel_requested"
            )
            if record.tool_execution == "running":
                record.tool_execution = "cancel_requested"
            record.delivery = "partial" if record.delivery != "full" else "full"
            record.cancellation_reason = reason[:240] or "user_interruption"
            record.terminal_version += 1
            record.updated_at = _utcnow()
            self._save()
            return record.model_copy(deep=True)

    def mark_provider_cancelled(self, assistant_turn_id: str) -> AssistantTurnRecord | None:
        return self._transition(
            assistant_turn_id,
            provider_execution="cancelled",
            allow_terminal=True,
        )

    def mark_provider_uninterruptible(self, assistant_turn_id: str) -> AssistantTurnRecord | None:
        return self._transition(
            assistant_turn_id,
            provider_execution="uninterruptible",
            allow_terminal=True,
        )

    def try_complete(self, assistant_turn_id: str, *, delivery: DeliveryState = "full") -> bool:
        with self._lock:
            record = self._records.get(assistant_turn_id)
            if record is None or record.terminal:
                return False
            record.lifecycle = "completed"
            record.provider_execution = "completed"
            record.delivery = delivery
            record.terminal_version += 1
            record.updated_at = _utcnow()
            self._save()
            return True

    def mark_failed(self, assistant_turn_id: str) -> AssistantTurnRecord | None:
        with self._lock:
            record = self._records.get(assistant_turn_id)
            if record is None:
                return None
            if record.terminal:
                return record.model_copy(deep=True)
            record.lifecycle = "failed"
            record.terminal_version += 1
            record.updated_at = _utcnow()
            self._save()
            return record.model_copy(deep=True)

    def is_cancelled(self, assistant_turn_id: str | None) -> bool:
        if not assistant_turn_id:
            return False
        record = self.get(assistant_turn_id)
        return bool(record and record.lifecycle == "interrupted")

    def _transition(
        self,
        assistant_turn_id: str,
        *,
        lifecycle: AssistantLifecycle | None = None,
        provider_execution: ProviderExecution | None = None,
        allow_terminal: bool,
    ) -> AssistantTurnRecord | None:
        with self._lock:
            record = self._records.get(assistant_turn_id)
            if record is None:
                return None
            if record.terminal and not allow_terminal:
                return record.model_copy(deep=True)
            if lifecycle is not None:
                record.lifecycle = lifecycle
            if provider_execution is not None:
                record.provider_execution = provider_execution
            record.updated_at = _utcnow()
            self._save()
            return record.model_copy(deep=True)

    def _load(self) -> dict[str, AssistantTurnRecord]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        records: dict[str, AssistantTurnRecord] = {}
        for item in payload if isinstance(payload, list) else []:
            try:
                record = AssistantTurnRecord.model_validate(item)
            except Exception:
                logger.debug("suppressed error in %s", "AssistantTurnCoordinator._load", exc_info=True)
                continue
            records[record.assistant_turn_id] = record
        return records

    def _save(self) -> None:
        payload = [
            record.model_dump(mode="json")
            for record in sorted(self._records.values(), key=lambda item: item.created_at)
        ]
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)


_default_coordinator: AssistantTurnCoordinator | None = None
_default_lock = threading.Lock()


def default_assistant_turn_coordinator(database=None) -> AssistantTurnCoordinator:
    """Return a coordinator bound to the caller's durable database authority."""
    global _default_coordinator
    if database is not None:
        with _default_lock:
            coordinator = getattr(database, "_assistant_turn_coordinator", None)
            if coordinator is None:
                from app.platform.chat.persistence.assistant_turn_store import PostgresAssistantTurnCoordinator

                coordinator = PostgresAssistantTurnCoordinator(database=database)
                database._assistant_turn_coordinator = coordinator
            return coordinator
    if _default_coordinator is not None:
        return _default_coordinator
    with _default_lock:
        if _default_coordinator is None:
            from app.persistence.runtime import uses_postgresql_runtime
            if uses_postgresql_runtime():
                from app.platform.chat.persistence.assistant_turn_store import PostgresAssistantTurnCoordinator
                _default_coordinator = PostgresAssistantTurnCoordinator()
            else:
                _default_coordinator = AssistantTurnCoordinator()
    return _default_coordinator
