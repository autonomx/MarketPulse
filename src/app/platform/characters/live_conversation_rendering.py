"""Live Chat speech delivery planning and session pronunciation persistence."""
from __future__ import annotations

from app.config.env import env_str, environment

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.conversation.performance_contract import SpeechPerformancePlan

SpeechDeliveryPlan = SpeechPerformancePlan


class SpeechDeliveryPlanRequest(BaseModel):
    """Compatibility request for the browser-authoritative delivery policy."""

    text: str = Field(min_length=1, max_length=4_000)
    stance: str = Field(default="automatic", max_length=40)
    presence_preset: str = Field(default="natural", max_length=40)
    conversation_pace: str = Field(default="balanced", max_length=40)
    emotional_attunement: str = Field(default="subtle", max_length=40)
    response_length: str = Field(default="conversational", max_length=40)
    response_onset_style: str = Field(default="adaptive", max_length=40)
    assistant_backchannel_mode: str = Field(default="off", max_length=40)
    serious: bool = False


class PronunciationEntry(BaseModel):
    id: str
    phrase: str = Field(min_length=1, max_length=120)
    pronunciation: str = Field(min_length=1, max_length=160)
    locale: str = Field(default="en-US", min_length=2, max_length=20)
    created_at: str
    updated_at: str


class PronunciationCreateRequest(BaseModel):
    phrase: str = Field(min_length=1, max_length=120)
    pronunciation: str = Field(min_length=1, max_length=160)
    locale: str = Field(default="en-US", min_length=2, max_length=20)


class PronunciationListResponse(BaseModel):
    session_id: str
    entries: list[PronunciationEntry] = Field(default_factory=list)


def create_speech_delivery_plan(request: SpeechDeliveryPlanRequest) -> SpeechDeliveryPlan:
    """Mirror the TypeScript policy for compatibility and parity fixtures.

    Runtime live-voice planning is browser authoritative because the browser owns
    clause commitment, scheduler state, and the active conversation profile.
    """

    text = request.text.strip()
    lower = text.lower()
    speech_act = "question" if text.endswith("?") else "answer"
    reassurance_markers = (
        "i understand",
        "that sounds",
        "take your time",
        "i'm sorry",
        "i am sorry",
    )
    if any(token in lower for token in reassurance_markers):
        speech_act = "reassurance"
    elif request.stance == "listen":
        speech_act = "reflection"
    elif request.stance in {"teach", "advise"}:
        speech_act = "instruction"
    elif len(text.split()) <= 4:
        speech_act = "acknowledgement"

    reflective = request.serious or speech_act in {"reassurance", "reflection"}
    energy = (
        "low"
        if reflective
        else "high"
        if request.presence_preset == "engaged"
        else "moderate"
    )
    warmth = (
        "high"
        if request.serious or request.emotional_attunement == "expressive"
        else "low"
        if request.emotional_attunement == "off"
        else "moderate"
    )
    uncertain_markers = ("maybe", "perhaps", "might", "not sure", "uncertain")
    certainty = (
        "low"
        if any(token in lower for token in uncertain_markers)
        else "high"
        if speech_act in {"answer", "instruction"}
        else "moderate"
    )
    pace = (
        "slightly_slow"
        if request.serious or request.response_length == "detailed"
        else "slightly_fast"
        if request.conversation_pace == "quick"
        else "natural"
    )
    clause_pause = (
        "long"
        if request.serious
        else "short"
        if speech_act == "acknowledgement"
        else "medium"
    )
    emphasis = [
        word.strip(".,!?;:")
        for word in text.split()
        if word.strip(".,!?;:").isupper() and len(word.strip(".,!?;:")) > 1
    ][:6]
    desired_onset_ms = (
        220
        if request.response_onset_style == "immediate"
        else 650
        if request.response_onset_style == "reflective"
        else 450
    )
    maximum_delay_ms = 120 if request.response_onset_style == "immediate" else 350
    return SpeechDeliveryPlan(
        speech_act=speech_act,
        energy=energy,
        warmth=warmth,
        certainty=certainty,
        pace=pace,
        clause_pause=clause_pause,
        emphasis=emphasis,
        onset_policy={
            "desired_perceived_onset_ms": desired_onset_ms,
            "maximum_additional_delay_ms": maximum_delay_ms,
        },
        nonverbal_eligibility={
            "breath": request.emotional_attunement != "off",
            "acknowledgement": request.assistant_backchannel_mode != "off",
            "amused_exhale": (
                request.emotional_attunement == "expressive" and not request.serious
            ),
            "sigh": request.emotional_attunement == "expressive" and request.serious,
        },
    )


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_pronunciation_path() -> Path:
    configured = env_str("OMNIX_LIVE_PRONUNCIATION_PATH", "").strip()
    return (
        Path(configured)
        if configured
        else Path("resources/data/live-conversation-pronunciations.json")
    )


class PronunciationStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_pronunciation_path()
        self._lock = threading.RLock()

    def list(self, session_id: str) -> PronunciationListResponse:
        with self._lock:
            data = self._read()
            entries = [
                PronunciationEntry.model_validate(item)
                for item in data.get(session_id, [])
            ]
            return PronunciationListResponse(session_id=session_id, entries=entries)

    def create(
        self,
        session_id: str,
        request: PronunciationCreateRequest,
    ) -> PronunciationListResponse:
        phrase = request.phrase.strip()
        pronunciation = request.pronunciation.strip()
        now = _utcnow()
        with self._lock:
            data = self._read()
            entries = [
                PronunciationEntry.model_validate(item)
                for item in data.get(session_id, [])
            ]
            matching = next(
                (
                    entry
                    for entry in entries
                    if entry.phrase.casefold() == phrase.casefold()
                ),
                None,
            )
            if matching:
                matching.phrase = phrase
                matching.pronunciation = pronunciation
                matching.locale = request.locale
                matching.updated_at = now
            else:
                entries.append(
                    PronunciationEntry(
                        id=f"pronunciation:{uuid.uuid4().hex}",
                        phrase=phrase,
                        pronunciation=pronunciation,
                        locale=request.locale,
                        created_at=now,
                        updated_at=now,
                    )
                )
            data[session_id] = [entry.model_dump(mode="json") for entry in entries]
            self._write(data)
            return PronunciationListResponse(session_id=session_id, entries=entries)

    def delete(self, session_id: str, entry_id: str) -> PronunciationListResponse:
        with self._lock:
            data = self._read()
            entries = [
                PronunciationEntry.model_validate(item)
                for item in data.get(session_id, [])
            ]
            data[session_id] = [entry for entry in entries if entry.id != entry_id]
            data[session_id] = [entry.model_dump(mode="json") for entry in data[session_id]]
            self._write(data)
            return self.list(session_id)

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    def _write(self, payload: dict[str, list[dict[str, Any]]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)


def default_pronunciation_store() -> PronunciationStore:
    return PronunciationStore()


def register_live_conversation_rendering_routes(
    router: APIRouter,
    *,
    chat_store_factory: Callable[[], Any] | None = None,
    pronunciation_store_factory: Callable[[], PronunciationStore] = (
        default_pronunciation_store
    ),
) -> None:
    def require_session(session_id: str) -> None:
        if chat_store_factory is None or chat_store_factory().get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="chat session not found")

    @router.post(
        "/api/chat/sessions/{session_id}/live-conversation/delivery-plan",
        response_model=SpeechDeliveryPlan,
        tags=["live-chat"],
    )
    def delivery_plan(
        session_id: str,
        request: SpeechDeliveryPlanRequest,
    ) -> SpeechDeliveryPlan:
        require_session(session_id)
        return create_speech_delivery_plan(request)

    @router.get(
        "/api/chat/sessions/{session_id}/live-conversation/pronunciations",
        response_model=PronunciationListResponse,
        tags=["live-chat"],
    )
    def list_pronunciations(session_id: str) -> PronunciationListResponse:
        require_session(session_id)
        return pronunciation_store_factory().list(session_id)

    @router.post(
        "/api/chat/sessions/{session_id}/live-conversation/pronunciations",
        response_model=PronunciationListResponse,
        tags=["live-chat"],
    )
    def create_pronunciation(
        session_id: str,
        request: PronunciationCreateRequest,
    ) -> PronunciationListResponse:
        require_session(session_id)
        return pronunciation_store_factory().create(session_id, request)

    @router.delete(
        "/api/chat/sessions/{session_id}/live-conversation/pronunciations/{entry_id}",
        response_model=PronunciationListResponse,
        tags=["live-chat"],
    )
    def delete_pronunciation(
        session_id: str,
        entry_id: str,
    ) -> PronunciationListResponse:
        require_session(session_id)
        return pronunciation_store_factory().delete(session_id, entry_id)
