"""Server-owned realtime speech session service."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from uuid import uuid4

from .cancel_scope import CancelScope
from .events import LiveSpeechEvent, LiveSpeechSessionConfig, error_event, event
from .llm import EchoTextGenerator, StreamingTextGenerator
from .metrics import LiveSpeechMetrics
from app.platform.voice.contracts import StreamingSpeechSynthesizer, StreamingTranscriber

from .stt import BufferedStreamingTranscriber
from .tts import DeterministicSpeechSynthesizer, split_text_for_tts
from .vad import EnergyVad


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass
class LiveSpeechRealtimeService:
    session_id: str = field(default_factory=lambda: _new_id("sess"))
    config: LiveSpeechSessionConfig = field(default_factory=LiveSpeechSessionConfig)
    cancel_scope: CancelScope = field(default_factory=CancelScope)
    transcriber: StreamingTranscriber = field(default_factory=BufferedStreamingTranscriber)
    synthesizer: StreamingSpeechSynthesizer = field(default_factory=DeterministicSpeechSynthesizer)
    text_generator: StreamingTextGenerator = field(default_factory=EchoTextGenerator)
    vad: EnergyVad | None = None
    metrics: LiveSpeechMetrics = field(default_factory=LiveSpeechMetrics)
    turn_id: str = field(default_factory=lambda: _new_id("turn"))
    response_id: str | None = None
    last_transcript: str = ""
    response_active: bool = False

    def __post_init__(self) -> None:
        if self.vad is None:
            self.vad = EnergyVad(
                threshold=self.config.turn_detection.threshold,
                silence_duration_ms=self.config.turn_detection.silence_duration_ms,
                sample_rate=self.config.input_audio_format.sample_rate,
            )

    @property
    def generation(self) -> int:
        return self.cancel_scope.generation

    def session_created(self) -> LiveSpeechEvent:
        return event(
            "session.created",
            session_id=self.session_id,
            generation=self.generation,
            session={"id": self.session_id, **self.config.model_dump(mode="json")},
        )

    def update_session(self, patch: dict) -> list[LiveSpeechEvent]:
        self.config = self.config.merged(patch)
        self.vad = EnergyVad(
            threshold=self.config.turn_detection.threshold,
            silence_duration_ms=self.config.turn_detection.silence_duration_ms,
            sample_rate=self.config.input_audio_format.sample_rate,
        )
        return [
            event(
                "session.updated",
                session_id=self.session_id,
                generation=self.generation,
                session={"id": self.session_id, **self.config.model_dump(mode="json")},
            )
        ]

    def append_audio_b64(self, audio_b64: str) -> list[LiveSpeechEvent]:
        try:
            pcm = base64.b64decode(audio_b64)
        except Exception:
            return [error_event(session_id=self.session_id, code="invalid_audio", message="audio must be base64 PCM")]
        return self.append_audio(pcm)

    def append_audio(self, pcm: bytes) -> list[LiveSpeechEvent]:
        events: list[LiveSpeechEvent] = []
        self.metrics.mark("first_audio_append")
        if not pcm:
            return events

        vad_result = self.vad.accept_pcm16(pcm) if self.vad else None
        if vad_result and vad_result.transition == "speech_started":
            self.metrics.mark("speech_started")
            events.append(
                event(
                    "input_audio_buffer.speech_started",
                    session_id=self.session_id,
                    turn_id=self.turn_id,
                    generation=self.generation,
                    audio_start_ms=self.metrics.speech_started_ms,
                    rms=vad_result.rms,
                )
            )
            if self.response_active and self.config.turn_detection.interrupt_response:
                events.extend(self.cancel_response(reason="turn_detected"))

        for update in self.transcriber.accept_audio(pcm):
            if not self.config.enable_live_transcription:
                continue
            self.metrics.mark("first_transcript_delta")
            self.last_transcript = update.text
            events.append(
                event(
                    "conversation.item.input_audio_transcription.delta",
                    session_id=self.session_id,
                    turn_id=self.turn_id,
                    generation=self.generation,
                    delta=update.text,
                    confidence=update.confidence,
                )
            )

        if vad_result and vad_result.transition == "speech_stopped":
            self.metrics.mark("speech_stopped")
            events.append(
                event(
                    "input_audio_buffer.speech_stopped",
                    session_id=self.session_id,
                    turn_id=self.turn_id,
                    generation=self.generation,
                    audio_end_ms=self.metrics.speech_stopped_ms,
                    rms=vad_result.rms,
                )
            )
            events.extend(self.finalize_transcript())
        return events

    def finalize_transcript(self) -> list[LiveSpeechEvent]:
        update = self.transcriber.finalize()
        self.metrics.mark("final_transcript")
        self.last_transcript = update.text
        return [
            event(
                "conversation.item.input_audio_transcription.completed",
                session_id=self.session_id,
                turn_id=self.turn_id,
                generation=self.generation,
                transcript=update.text,
                confidence=update.confidence,
                duration_ms=update.duration_ms,
            )
        ]

    def inject_text(self, text: str) -> list[LiveSpeechEvent]:
        self.last_transcript = text.strip()
        return [
            event(
                "conversation.item.created",
                session_id=self.session_id,
                turn_id=self.turn_id,
                generation=self.generation,
                item={"type": "message", "role": "user", "content": [{"type": "input_text", "text": self.last_transcript}]},
            )
        ]

    def create_response(self, instructions: str | None = None) -> list[LiveSpeechEvent]:
        if self.response_active:
            return [error_event(session_id=self.session_id, code="conversation_already_has_active_response", message="another response is in progress", generation=self.generation)]

        self.response_active = True
        self.response_id = _new_id("resp")
        generation = self.generation
        prompt_text = self.last_transcript or "Hello."
        self.metrics.mark("response_created")
        events: list[LiveSpeechEvent] = [
            event(
                "response.created",
                session_id=self.session_id,
                turn_id=self.turn_id,
                response_id=self.response_id,
                generation=generation,
                response={"id": self.response_id, "status": "in_progress"},
            )
        ]

        transcript_parts: list[str] = []
        text_index = 0
        for generated in self.text_generator.generate(prompt_text, instructions=instructions or self.config.instructions, generation=generation):
            if self.cancel_scope.should_drop(generation):
                self.metrics.drop_stale_chunk()
                continue
            transcript_parts.append(generated)
            for text_chunk in split_text_for_tts(generated, max_chars=140):
                if self.cancel_scope.should_drop(generation):
                    self.metrics.drop_stale_chunk()
                    continue
                self.metrics.mark("first_text_delta")
                events.append(
                    event(
                        "response.text.delta",
                        session_id=self.session_id,
                        turn_id=self.turn_id,
                        response_id=self.response_id,
                        generation=generation,
                        delta=text_chunk,
                        index=text_index,
                    )
                )
                text_index += 1
                self.metrics.queued_text_chunks += 1
                for audio in self.synthesizer.synthesize(text_chunk, voice=self.config.voice, generation=generation):
                    if self.cancel_scope.should_drop(generation):
                        self.metrics.drop_stale_chunk()
                        continue
                    self.metrics.mark("first_audio_delta")
                    self.metrics.queued_audio_chunks += 1
                    events.append(
                        event(
                            "response.output_audio.delta",
                            session_id=self.session_id,
                            turn_id=self.turn_id,
                            response_id=self.response_id,
                            generation=generation,
                            delta=audio.b64(),
                            sample_rate=audio.sample_rate,
                            sequence=audio.sequence,
                        )
                    )

        events.extend(self.finish_response(status="completed", generation=generation, transcript="".join(transcript_parts)))
        return events

    def cancel_response(self, reason: str = "client_cancelled") -> list[LiveSpeechEvent]:
        previous_response_id = self.response_id
        next_generation = self.cancel_scope.cancel(reason)
        self.metrics.mark("cancelled")
        self.response_active = False
        return [
            event("response.output_audio.done", session_id=self.session_id, turn_id=self.turn_id, response_id=previous_response_id, generation=next_generation),
            event(
                "response.done",
                session_id=self.session_id,
                turn_id=self.turn_id,
                response_id=previous_response_id,
                generation=next_generation,
                response={"id": previous_response_id, "status": "cancelled", "status_details": {"reason": reason}},
            ),
        ]

    def finish_response(self, *, status: str, generation: int, transcript: str) -> list[LiveSpeechEvent]:
        response_id = self.response_id
        self.metrics.mark("output_audio_done")
        self.metrics.mark("response_done")
        self.response_active = False
        self.cancel_scope.response_done(generation)
        events = [
            event("response.output_audio.done", session_id=self.session_id, turn_id=self.turn_id, response_id=response_id, generation=generation),
            event("response.output_audio_transcript.done", session_id=self.session_id, turn_id=self.turn_id, response_id=response_id, generation=generation, transcript=transcript),
            event("response.metrics", session_id=self.session_id, turn_id=self.turn_id, response_id=response_id, generation=generation, metrics=self.metrics.payload()),
            event("response.done", session_id=self.session_id, turn_id=self.turn_id, response_id=response_id, generation=generation, response={"id": response_id, "status": status}),
        ]
        self.turn_id = _new_id("turn")
        self.transcriber.reset()
        self.last_transcript = ""
        self.response_id = None
        self.metrics = LiveSpeechMetrics(session_started_ms=self.metrics.session_started_ms)
        return events
