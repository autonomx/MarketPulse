"""Typed realtime event contracts for Omnix live speech.

The event names intentionally mirror the OpenAI/HF realtime surface where
practical while keeping Omnix-specific metadata additive.
"""
from __future__ import annotations

import time
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

LiveSpeechEventType = Literal[
    "session.created",
    "session.updated",
    "input_audio_buffer.speech_started",
    "input_audio_buffer.speech_stopped",
    "conversation.item.created",
    "conversation.item.input_audio_transcription.delta",
    "conversation.item.input_audio_transcription.completed",
    "response.created",
    "response.text.delta",
    "response.output_audio.delta",
    "response.output_audio.done",
    "response.output_audio_transcript.done",
    "response.done",
    "response.metrics",
    "error",
]


class LiveSpeechTurnDetection(BaseModel):
    type: Literal["server_vad", "client_vad", "disabled"] = "server_vad"
    threshold: float = 0.008
    silence_duration_ms: int = 500
    interrupt_response: bool = True


class LiveSpeechAudioFormat(BaseModel):
    sample_rate: int = 16000
    channels: int = 1
    sample_width_bytes: int = 2
    encoding: Literal["pcm16", "float32"] = "pcm16"


class LiveSpeechSessionConfig(BaseModel):
    model: str = "omnix-live-speech"
    instructions: str = ""
    voice: str = "default"
    input_audio_format: LiveSpeechAudioFormat = Field(default_factory=LiveSpeechAudioFormat)
    output_audio_format: LiveSpeechAudioFormat = Field(
        default_factory=lambda: LiveSpeechAudioFormat(sample_rate=24000, encoding="pcm16")
    )
    turn_detection: LiveSpeechTurnDetection = Field(default_factory=LiveSpeechTurnDetection)
    enable_live_transcription: bool = True
    max_response_text_chars: int = 1600

    def merged(self, patch: dict[str, Any]) -> "LiveSpeechSessionConfig":
        data = self.model_dump(mode="json")
        _deep_merge(data, patch)
        return LiveSpeechSessionConfig.model_validate(data)


class LiveSpeechEvent(BaseModel):
    type: LiveSpeechEventType
    event_id: str = Field(default_factory=lambda: f"event_{uuid4().hex}")
    session_id: str
    turn_id: str | None = None
    response_id: str | None = None
    generation: int = 0
    created_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    payload: dict[str, Any] = Field(default_factory=dict)

    def wire(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        payload = data.pop("payload")
        data.update(payload)
        return data


class LiveSpeechProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def event(
    event_type: LiveSpeechEventType,
    *,
    session_id: str,
    turn_id: str | None = None,
    response_id: str | None = None,
    generation: int = 0,
    **payload: Any,
) -> LiveSpeechEvent:
    return LiveSpeechEvent(
        type=event_type,
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        generation=generation,
        payload=payload,
    )


def error_event(*, session_id: str, code: str, message: str, generation: int = 0) -> LiveSpeechEvent:
    return event("error", session_id=session_id, generation=generation, error={"type": code, "message": message})


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
