"""Typed HTTP contracts for live speech status and compatibility metadata."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _LiveSpeechResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LiveSpeechCompatibleEvents(_LiveSpeechResponse):
    client_to_server: list[str]
    server_to_client: list[str]


class LiveSpeechProtocolResponse(_LiveSpeechResponse):
    ok: bool = True
    contract: Literal["omnix_live_speech_realtime_v1"]
    preferred_socket_path: str
    compatibility_target: Literal["openai_hf_realtime_subset"]
    events: LiveSpeechCompatibleEvents
    notes: list[str]


class LiveSpeechProviderStatus(_LiveSpeechResponse):
    stt: str
    tts: str
    llm: str
    vad: str


class LiveSpeechSampleRates(_LiveSpeechResponse):
    input_hz: int
    output_hz: int


class LiveSpeechStatusResponse(_LiveSpeechResponse):
    ok: bool
    enabled: bool
    socket_path: str
    contract: Literal["omnix_live_speech_realtime_v1"]
    providers: LiveSpeechProviderStatus
    sample_rates: LiveSpeechSampleRates
