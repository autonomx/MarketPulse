"""Production STT adapter seams for live speech.

The default test transcriber remains dependency-free. These adapters let runtime
configuration swap in a real Parakeet-compatible service without changing the
realtime protocol or service tests.
"""
from __future__ import annotations

from app.config.env import env_str, environment

import os
import wave
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from app.runtime.http_client import shared_http_client
from app.security.service_token import service_headers

from app.platform.voice.contracts import StreamingTranscriber, TranscriptUpdate

from .stt import BufferedStreamingTranscriber


@dataclass
class ParakeetServiceTranscriber(StreamingTranscriber):
    """Adapter for an external Parakeet-compatible transcription service.

    The current Omnix Parakeet sidecar historically exposes a final-transcript
    HTTP endpoint. This adapter keeps realtime partials alive locally while using
    the real service for final text when available. When the sidecar gains true
    partial events, this class can be replaced by an async websocket variant
    behind the same ``StreamingTranscriber`` contract.
    """

    base_url: str = "http://127.0.0.1:8000"
    partial_every_bytes: int = 6400
    sample_rate: int = 16000
    timeout_seconds: float = 20.0
    fallback: BufferedStreamingTranscriber = field(default_factory=BufferedStreamingTranscriber)
    _buffer: bytearray = field(default_factory=bytearray)
    _last_partial_size: int = 0

    def accept_audio(self, pcm: bytes) -> list[TranscriptUpdate]:
        if not pcm:
            return []
        self._buffer.extend(pcm)
        self.fallback.accept_audio(pcm)
        if len(self._buffer) - self._last_partial_size >= self.partial_every_bytes:
            self._last_partial_size = len(self._buffer)
            duration_ms = int((len(self._buffer) / 2) / self.sample_rate * 1000)
            return [TranscriptUpdate(text=f"speech {duration_ms}ms", final=False, confidence=0.4, duration_ms=duration_ms)]
        return []

    def finalize(self) -> TranscriptUpdate:
        if not self._buffer:
            return TranscriptUpdate(text="", final=True, confidence=0.0, duration_ms=0)
        duration_ms = int((len(self._buffer) / 2) / self.sample_rate * 1000)
        try:
            text = self._transcribe_with_service(bytes(self._buffer))
        except Exception:
            text = self.fallback.finalize().text
        return TranscriptUpdate(text=text, final=True, confidence=0.8 if text else 0.0, duration_ms=duration_ms)

    def reset(self) -> None:
        self._buffer.clear()
        self._last_partial_size = 0
        self.fallback.reset()

    def _transcribe_with_service(self, pcm: bytes) -> str:
        wav_bytes = _pcm16_wav_bytes(pcm, sample_rate=self.sample_rate)
        response = shared_http_client("stt-service").post(
            f"{self.base_url.rstrip('/')}/transcribe",
            files={"file": ("utterance.wav", wav_bytes, "audio/wav")},
            timeout=self.timeout_seconds,
            headers=service_headers(),
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        value = payload.get("text") or payload.get("transcript") or payload.get("transcription") or ""
        return str(value).strip()


def create_transcriber_from_env() -> StreamingTranscriber:
    provider = environment().get("LIVE_SPEECH_STT_PROVIDER", "fake").strip().lower()
    if provider in {"parakeet", "parakeet_http", "real"}:
        return ParakeetServiceTranscriber(base_url=environment().get("LIVE_SPEECH_STT_URL", "http://127.0.0.1:8000"))
    return BufferedStreamingTranscriber()


def _pcm16_wav_bytes(pcm: bytes, *, sample_rate: int) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()
