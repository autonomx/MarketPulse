"""Quality-first finalization policy for Nemotron + Parakeet EOU.

Live Nemotron hypotheses stay available for draft UI and endpoint coordination,
but the submitted user transcript is decoded once from the complete buffered
utterance. This prevents a short or fragmented streaming hypothesis from
becoming the authoritative sentence when Parakeet closes the turn.
"""
from __future__ import annotations

import time
from typing import Any

from app.providers.nemotron_eou_streaming import (
    NemotronEouModelManager,
    _configure_streaming_context,
    env_int,
)

_SUPPORTED_FINAL_RIGHT_CONTEXTS = {0, 1, 6, 13}


class QualityFirstNemotronEouModelManager(NemotronEouModelManager):
    """Use a higher-context full-buffer Nemotron decode for authoritative finals."""

    def __init__(self) -> None:
        super().__init__()
        # Keep the live stream at the low-latency right context (normally 1 =
        # 160 ms), but decode the completed utterance with Nemotron's largest
        # supported right context by default. The shared model is reconfigured
        # only while holding the Nemotron lock, so this adds no duplicate model
        # or VRAM allocation and cannot race a streaming feed.
        requested = env_int("OMNIX_NEMOTRON_FINAL_RIGHT_CONTEXT", 13)
        self.nemotron_final_right_context = (
            requested if requested in _SUPPORTED_FINAL_RIGHT_CONTEXTS else 13
        )

    def health_details(self) -> dict[str, Any]:
        return {
            **super().health_details(),
            "nemotron_final_right_context": self.nemotron_final_right_context,
        }

    def _transcribe_quality_pcm16(self, pcm16le: bytes) -> str:
        # Unit tests commonly replace transcribe_pcm16 without loading NeMo.
        # Production finalization always has a loaded model because the segment
        # has already streamed through Nemotron.
        if self.nemotron_model is None:
            return self.transcribe_pcm16(pcm16le)

        with self._nemotron_lock:
            _configure_streaming_context(self.nemotron_model, self.nemotron_final_right_context)
            try:
                return self.transcribe_pcm16(pcm16le)
            finally:
                # Restore the live configuration before the next microphone
                # chunk can enter the shared Nemotron model.
                _configure_streaming_context(self.nemotron_model, self.nemotron_right_context)

    def finalize(
        self,
        segment_id: str,
        pcm16le_fallback: bytes = b"",
    ) -> tuple[str, dict[str, float]]:
        stream = self._streams.get(segment_id)
        streaming_text = stream.nemotron.finalize_text() if stream is not None else ""
        final_context = float(self.nemotron_final_right_context)

        if not pcm16le_fallback:
            return streaming_text, {
                "authoritative_full_decode": 0.0,
                "streaming_final": 1.0 if streaming_text else 0.0,
                "offline_fallback": 0.0,
                "streaming_chars": float(len(streaming_text)),
                "authoritative_chars": float(len(streaming_text)),
                "authoritative_changed": 0.0,
                "final_right_context": final_context,
            }

        started = time.perf_counter()
        try:
            authoritative_text = self._transcribe_quality_pcm16(pcm16le_fallback).strip()
        except Exception:
            if not streaming_text:
                raise
            return streaming_text, {
                "authoritative_full_decode": 0.0,
                "full_decode_failed": 1.0,
                "streaming_final": 1.0,
                "offline_fallback": 0.0,
                "streaming_chars": float(len(streaming_text)),
                "authoritative_chars": float(len(streaming_text)),
                "authoritative_changed": 0.0,
                "final_right_context": final_context,
            }

        full_decode_ms = (time.perf_counter() - started) * 1000.0
        if authoritative_text:
            return authoritative_text, {
                "authoritative_full_decode": 1.0,
                "full_decode_ms": round(full_decode_ms, 3),
                "streaming_final": 0.0,
                "offline_fallback": 0.0,
                "streaming_chars": float(len(streaming_text)),
                "authoritative_chars": float(len(authoritative_text)),
                "authoritative_changed": 1.0 if authoritative_text != streaming_text else 0.0,
                "final_right_context": final_context,
            }

        return streaming_text, {
            "authoritative_full_decode": 0.0,
            "full_decode_empty": 1.0,
            "full_decode_ms": round(full_decode_ms, 3),
            "streaming_final": 1.0 if streaming_text else 0.0,
            "offline_fallback": 0.0,
            "streaming_chars": float(len(streaming_text)),
            "authoritative_chars": float(len(streaming_text)),
            "authoritative_changed": 0.0,
            "final_right_context": final_context,
        }

    def preview(self, pcm16le: bytes) -> tuple[str, dict[str, float]]:
        """Decode a side-effect-free high-context snapshot during a pause."""

        started = time.perf_counter()
        text = self._transcribe_quality_pcm16(pcm16le).strip() if pcm16le else ""
        decode_ms = (time.perf_counter() - started) * 1000.0
        return text, {
            "authoritative_preview": 1.0,
            "preview_decode_ms": round(decode_ms, 3),
            "final_right_context": float(self.nemotron_final_right_context),
        }

    def finalize_from_preview(
        self,
        segment_id: str,
        preview_text: str,
        preview_decode_ms: float,
    ) -> tuple[str, dict[str, float]]:
        """Promote a preview proven to have only silence after its snapshot."""

        stream = self._streams.get(segment_id)
        streaming_text = stream.nemotron.finalize_text() if stream is not None else ""
        return preview_text.strip(), {
            "authoritative_full_decode": 1.0,
            "authoritative_preview_reused": 1.0,
            "preview_decode_ms": round(preview_decode_ms, 3),
            "full_decode_ms": round(preview_decode_ms, 3),
            "streaming_final": 0.0,
            "offline_fallback": 0.0,
            "streaming_chars": float(len(streaming_text)),
            "authoritative_chars": float(len(preview_text.strip())),
            "authoritative_changed": 1.0 if preview_text.strip() != streaming_text else 0.0,
            "final_right_context": float(self.nemotron_final_right_context),
        }


quality_model_manager = QualityFirstNemotronEouModelManager()
