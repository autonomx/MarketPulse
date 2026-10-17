"""Deterministic TTS on the speech contract's session port, and TTS text splitting (PA-3.3)."""
from __future__ import annotations

from app.platform.voice.contracts import AudioDelta, StreamingSpeechSynthesizer


class DeterministicSpeechSynthesizer(StreamingSpeechSynthesizer):
    """Small fake TTS provider for protocol tests and offline gateway use."""

    def __init__(self, frame_samples: int = 2400) -> None:
        self.frame_samples = frame_samples
        self._sequence = 0

    def synthesize(self, text: str, *, voice: str = "default", generation: int = 0) -> list[AudioDelta]:
        if not text.strip():
            return []
        # Deterministic low-amplitude PCM16 square-ish frame; not intended for
        # quality playback, only for contract-level streaming and tests.
        sample = (512).to_bytes(2, byteorder="little", signed=True)
        pcm = sample * self.frame_samples
        delta = AudioDelta(pcm=pcm, sequence=self._sequence)
        self._sequence += 1
        return [delta]


def split_text_for_tts(text: str, *, max_chars: int = 160) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    chunks: list[str] = []
    buffer = ""
    for token in text.split():
        candidate = f"{buffer} {token}".strip()
        if len(candidate) > max_chars and buffer:
            chunks.append(buffer.strip())
            # A single overlong token must still be bounded for the TTS
            # service. Split it into fixed-size pieces rather than emitting an
            # unbounded request.
            while len(token) > max_chars:
                chunks.append(token[:max_chars])
                token = token[max_chars:]
            buffer = token
        else:
            buffer = candidate
        if buffer.endswith(('.', '!', '?', ',')) and len(buffer) >= 24:
            chunks.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        chunks.append(buffer.strip())
    return chunks
