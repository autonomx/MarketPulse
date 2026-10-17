"""Server-side voice activity detection for realtime speech."""
from __future__ import annotations

import audioop
from dataclasses import dataclass
from typing import Literal

VadTransition = Literal["speech_started", "speech_stopped"]


@dataclass
class VadResult:
    transition: VadTransition | None
    is_speaking: bool
    rms: float


class EnergyVad:
    """Small deterministic VAD suitable for tests and fallback runtime use.

    The implementation intentionally avoids heavyweight model dependencies. A
    Silero or provider-backed VAD can be added behind the same contract later.
    """

    def __init__(self, threshold: float = 0.008, silence_duration_ms: int = 500, sample_rate: int = 16000) -> None:
        self.threshold = threshold
        self.silence_duration_ms = silence_duration_ms
        self.sample_rate = sample_rate
        self.is_speaking = False
        self._silence_ms = 0

    def accept_pcm16(self, pcm: bytes) -> VadResult:
        if not pcm:
            return VadResult(transition=None, is_speaking=self.is_speaking, rms=0.0)
        rms = audioop.rms(pcm, 2) / 32768.0
        chunk_ms = int((len(pcm) / 2) / self.sample_rate * 1000)
        if rms >= self.threshold:
            self._silence_ms = 0
            if not self.is_speaking:
                self.is_speaking = True
                return VadResult(transition="speech_started", is_speaking=True, rms=rms)
            return VadResult(transition=None, is_speaking=True, rms=rms)

        if self.is_speaking:
            self._silence_ms += max(1, chunk_ms)
            if self._silence_ms >= self.silence_duration_ms:
                self.is_speaking = False
                self._silence_ms = 0
                return VadResult(transition="speech_stopped", is_speaking=False, rms=rms)
        return VadResult(transition=None, is_speaking=self.is_speaking, rms=rms)

    def reset(self) -> None:
        self.is_speaking = False
        self._silence_ms = 0
