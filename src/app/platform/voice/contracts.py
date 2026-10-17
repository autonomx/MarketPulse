"""The speech capability's contract (ADR-0016, PA-3.3): STT and TTS session ports.

Voice, live voice and live speech build on these. A module imports speech
types from here, never from another speech module's internals. The wire
format of TTS streaming (request policy, PCM framing) is transport-neutral
and lives in the kernel, ``app.conversation.tts_stream_contract``.

- STT session: a ``StreamingTranscriber`` takes PCM as it arrives and yields
  partial and final ``TranscriptUpdate``s.
- TTS session: a ``StreamingSpeechSynthesizer`` turns text into ``AudioDelta``
  frames; live voice streams from a ``TTSProvider`` through a ``TTSLane``.
"""
from __future__ import annotations

import base64
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class TranscriptUpdate:
    text: str
    final: bool = False
    confidence: float | None = None
    duration_ms: int | None = None


class StreamingTranscriber:
    """Interface for partial/final realtime transcript producers."""

    def accept_audio(self, pcm: bytes) -> list[TranscriptUpdate]:
        raise NotImplementedError

    def finalize(self) -> TranscriptUpdate:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError


@dataclass
class AudioDelta:
    pcm: bytes
    sample_rate: int = 24000
    sequence: int = 0

    def b64(self) -> str:
        return base64.b64encode(self.pcm).decode("ascii")


class StreamingSpeechSynthesizer:
    def synthesize(self, text: str, *, voice: str = "default", generation: int = 0) -> list[AudioDelta]:
        raise NotImplementedError


class TTSProvider(Protocol):
    provider_name: str

    def generate_audio_stream(
        self,
        *,
        text: str,
        speaker: str | None,
        language: str,
        **kwargs: Any,
    ) -> Iterator[tuple[Any, int, Any]]: ...


class TTSProviderResolver(Protocol):
    def get(self, provider_name: str | None = None) -> TTSProvider | None: ...


class TTSLane(Protocol):
    def stream(
        self,
        provider: TTSProvider,
        *,
        text: str,
        speaker: str | None,
        language: str,
        kwargs: dict[str, Any],
        priority: Any,
        **options: Any,
    ) -> Iterator[tuple[Any, int, Any]]: ...


__all__ = [
    "AudioDelta",
    "StreamingSpeechSynthesizer",
    "StreamingTranscriber",
    "TTSLane",
    "TTSProvider",
    "TTSProviderResolver",
    "TranscriptUpdate",
]
