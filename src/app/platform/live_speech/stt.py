"""Deterministic fallback STT on the speech contract's session port (PA-3.3)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.platform.voice.contracts import StreamingTranscriber, TranscriptUpdate


@dataclass
class BufferedStreamingTranscriber(StreamingTranscriber):
    """Buffered fallback that still exposes a streaming contract.

    Real Parakeet/nano-parakeet adapters can replace this class without changing
    the realtime WebSocket protocol. In fallback mode, deterministic partials are
    emitted as audio arrives so UI and pipeline code can be tested without an ASR
    dependency.
    """

    partial_text: str = "listening"
    final_text: str = "transcribed speech"
    partial_every_bytes: int = 3200
    _buffer: bytearray = field(default_factory=bytearray)
    _last_partial_size: int = 0

    def accept_audio(self, pcm: bytes) -> list[TranscriptUpdate]:
        if not pcm:
            return []
        self._buffer.extend(pcm)
        if len(self._buffer) - self._last_partial_size >= self.partial_every_bytes:
            self._last_partial_size = len(self._buffer)
            suffix = max(1, len(self._buffer) // self.partial_every_bytes)
            return [TranscriptUpdate(text=f"{self.partial_text} {suffix}", final=False, confidence=0.5)]
        return []

    def finalize(self) -> TranscriptUpdate:
        duration_ms = int((len(self._buffer) / 2) / 16000 * 1000) if self._buffer else 0
        text = self.final_text if self._buffer else ""
        return TranscriptUpdate(text=text, final=True, confidence=0.8 if text else 0.0, duration_ms=duration_ms)

    def reset(self) -> None:
        self._buffer.clear()
        self._last_partial_size = 0
