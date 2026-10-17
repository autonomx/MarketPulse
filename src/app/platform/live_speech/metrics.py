"""Latency metrics collected for a realtime speech turn."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.observability.metrics import record_speech_turn
from app.observability.tracing import record_span


_TURN_STAGES = {"final_transcript": "transcript", "first_audio_delta": "first_audio"}
# Trace spans (WP-10.4): the mark that ends a stage -> (span name, the mark that starts it).
_STAGE_SPANS = {
    "speech_stopped": ("live_speech.utterance", "speech_started"),
    "final_transcript": ("live_speech.transcript", "speech_stopped"),
    "first_audio_delta": ("live_speech.first_audio", "speech_stopped"),
    "response_done": ("live_speech.response", "response_created"),
}


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class LiveSpeechMetrics:
    session_started_ms: int = field(default_factory=now_ms)
    first_audio_append_ms: int | None = None
    speech_started_ms: int | None = None
    speech_stopped_ms: int | None = None
    first_transcript_delta_ms: int | None = None
    final_transcript_ms: int | None = None
    response_created_ms: int | None = None
    first_text_delta_ms: int | None = None
    first_audio_delta_ms: int | None = None
    output_audio_done_ms: int | None = None
    response_done_ms: int | None = None
    cancelled_ms: int | None = None
    stale_chunks_dropped: int = 0
    queued_text_chunks: int = 0
    queued_audio_chunks: int = 0

    def mark(self, name: str) -> None:
        field_name = f"{name}_ms"
        if hasattr(self, field_name) and getattr(self, field_name) is None:
            setattr(self, field_name, now_ms())
            stage = _TURN_STAGES.get(name)
            if stage is not None and self.speech_stopped_ms is not None:
                # Latency the user hears: from the end of their speech (WP-10.3).
                record_speech_turn(stage, (getattr(self, field_name) - self.speech_stopped_ms) / 1000)
            span = _STAGE_SPANS.get(name)
            started = getattr(self, f"{span[1]}_ms") if span is not None else None
            if span is not None and started is not None:
                record_span(span[0], start_ms=started, end_ms=getattr(self, field_name))

    def drop_stale_chunk(self) -> None:
        self.stale_chunks_dropped += 1

    def payload(self) -> dict[str, int | None]:
        return {
            "session_started_ms": self.session_started_ms,
            "first_audio_append_ms": self.first_audio_append_ms,
            "speech_started_ms": self.speech_started_ms,
            "speech_stopped_ms": self.speech_stopped_ms,
            "first_transcript_delta_ms": self.first_transcript_delta_ms,
            "final_transcript_ms": self.final_transcript_ms,
            "response_created_ms": self.response_created_ms,
            "first_text_delta_ms": self.first_text_delta_ms,
            "first_audio_delta_ms": self.first_audio_delta_ms,
            "output_audio_done_ms": self.output_audio_done_ms,
            "response_done_ms": self.response_done_ms,
            "cancelled_ms": self.cancelled_ms,
            "stale_chunks_dropped": self.stale_chunks_dropped,
            "queued_text_chunks": self.queued_text_chunks,
            "queued_audio_chunks": self.queued_audio_chunks,
        }
