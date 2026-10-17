"""Compatibility metadata for Omnix realtime live speech."""
from __future__ import annotations

OPENAI_REALTIME_COMPATIBLE_EVENTS = {
    "client_to_server": [
        "session.update",
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "conversation.item.create",
        "response.create",
        "response.cancel",
    ],
    "server_to_client": [
        "session.created",
        "session.updated",
        "input_audio_buffer.speech_started",
        "input_audio_buffer.speech_stopped",
        "conversation.item.created",
        "conversation.item.input_audio_transcription.delta",
        "conversation.item.input_audio_transcription.completed",
        "response.created",
        "response.text.delta",
        "response.output_audio.delta",
        "response.output_audio.done",
        "response.output_audio_transcript.done",
        "response.metrics",
        "response.done",
        "error",
    ],
}


def compatibility_payload() -> dict:
    return {
        "contract": "omnix_live_speech_realtime_v1",
        "preferred_socket_path": "/v1/realtime",
        "compatibility_target": "openai_hf_realtime_subset",
        "events": OPENAI_REALTIME_COMPATIBLE_EVENTS,
        "notes": [
            "Omnix adds response.metrics as an additive event.",
            "Binary audio transport may be added later; the v1 subset uses base64 PCM deltas.",
            "Standalone legacy STT/TTS routes may remain as fallback surfaces during migration.",
        ],
    }
