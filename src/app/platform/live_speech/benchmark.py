"""Backend benchmark helpers for live speech adapters."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from app.platform.voice.contracts import StreamingSpeechSynthesizer, StreamingTranscriber


class Clock(Protocol):
    def __call__(self) -> float: ...


@dataclass
class LiveSpeechBenchmarkResult:
    name: str
    first_transcript_delta_ms: float | None = None
    final_transcript_ms: float | None = None
    first_audio_delta_ms: float | None = None
    audio_chunk_count: int = 0
    transcript: str = ""

    def as_dict(self) -> dict[str, float | int | str | None]:
        return {
            "name": self.name,
            "first_transcript_delta_ms": self.first_transcript_delta_ms,
            "final_transcript_ms": self.final_transcript_ms,
            "first_audio_delta_ms": self.first_audio_delta_ms,
            "audio_chunk_count": self.audio_chunk_count,
            "transcript": self.transcript,
        }


def benchmark_stt(transcriber: StreamingTranscriber, pcm_chunks: list[bytes], *, name: str = "stt", clock: Clock = time.perf_counter) -> LiveSpeechBenchmarkResult:
    start = clock()
    first_delta_ms: float | None = None
    transcript = ""
    for chunk in pcm_chunks:
        for update in transcriber.accept_audio(chunk):
            if first_delta_ms is None:
                first_delta_ms = (clock() - start) * 1000
            transcript = update.text
    final = transcriber.finalize()
    final_ms = (clock() - start) * 1000
    return LiveSpeechBenchmarkResult(
        name=name,
        first_transcript_delta_ms=first_delta_ms,
        final_transcript_ms=final_ms,
        transcript=final.text or transcript,
    )


def benchmark_tts(synthesizer: StreamingSpeechSynthesizer, text: str, *, name: str = "tts", clock: Clock = time.perf_counter) -> LiveSpeechBenchmarkResult:
    start = clock()
    first_audio_ms: float | None = None
    chunks = synthesizer.synthesize(text)
    if chunks:
        first_audio_ms = (clock() - start) * 1000
    return LiveSpeechBenchmarkResult(
        name=name,
        first_audio_delta_ms=first_audio_ms,
        audio_chunk_count=len(chunks),
    )
