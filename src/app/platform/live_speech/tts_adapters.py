"""Production TTS adapter seams for live speech."""
from __future__ import annotations

from app.config.env import env_str, environment

import base64
import os
from dataclasses import dataclass
from typing import Any

import httpx

from app.runtime.http_client import shared_http_client
from app.security.service_token import service_headers

from app.platform.voice.contracts import AudioDelta, StreamingSpeechSynthesizer

from .tts import DeterministicSpeechSynthesizer


class SpeechServiceUnavailable(RuntimeError):
    """The TTS service did not return audio."""


@dataclass
class QwenServiceSpeechSynthesizer(StreamingSpeechSynthesizer):
    """Adapter for a Qwen-compatible TTS HTTP service.

    The adapter accepts either raw audio responses or JSON payloads containing
    base64 audio fields. It keeps the realtime protocol stable while production
    services evolve behind the same interface.
    """

    base_url: str = "http://127.0.0.1:5101"
    sample_rate: int = 24000
    timeout_seconds: float = 30.0
    _sequence: int = 0

    def synthesize(self, text: str, *, voice: str = "default", generation: int = 0) -> list[AudioDelta]:
        clean = text.strip()
        if not clean:
            return []
        payload = {"text": clean, "voice": voice, "sample_rate": self.sample_rate}
        try:
            response = shared_http_client("tts-service").post(
                f"{self.base_url.rstrip('/')}/tts", json=payload, timeout=self.timeout_seconds, headers=service_headers()
            )
            response.raise_for_status()
            pcm = self._decode_response(response)
        except Exception as exc:
            # No synthetic stand-in audio (WP-7.3): the caller reports the error.
            raise SpeechServiceUnavailable(f"TTS service request failed: {exc}") from exc
        if not pcm:
            return []
        delta = AudioDelta(pcm=pcm, sample_rate=self.sample_rate, sequence=self._sequence)
        self._sequence += 1
        return [delta]

    def _decode_response(self, response: httpx.Response) -> bytes:
        content_type = response.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            payload: dict[str, Any] = response.json()
            audio_value = payload.get("audio") or payload.get("audio_b64") or payload.get("pcm") or ""
            if isinstance(audio_value, str):
                return base64.b64decode(audio_value)
            raise ValueError("tts json response did not include base64 audio")
        return response.content


def create_synthesizer_from_env() -> StreamingSpeechSynthesizer:
    provider = environment().get("LIVE_SPEECH_TTS_PROVIDER", "fake").strip().lower()
    if provider in {"qwen", "qwen3", "qwen_http", "real"}:
        return QwenServiceSpeechSynthesizer(base_url=environment().get("LIVE_SPEECH_TTS_URL", "http://127.0.0.1:5101"))
    return DeterministicSpeechSynthesizer()
