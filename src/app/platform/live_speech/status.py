"""Runtime status helpers for live speech."""
from __future__ import annotations

from app.config.env import environment

import os

from .compat import compatibility_payload


def live_speech_enabled() -> bool:
    return environment().get("LIVE_SPEECH_REALTIME_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def live_speech_status_payload() -> dict:
    return {
        "ok": True,
        "enabled": live_speech_enabled(),
        "socket_path": "/v1/realtime",
        "contract": compatibility_payload()["contract"],
        "providers": {
            "stt": environment().get("LIVE_SPEECH_STT_PROVIDER", "fake"),
            "tts": environment().get("LIVE_SPEECH_TTS_PROVIDER", "fake"),
            "llm": environment().get("LIVE_SPEECH_LLM_PROVIDER", "fake"),
            "vad": environment().get("LIVE_SPEECH_VAD_PROVIDER", "energy"),
        },
        "sample_rates": {
            "input_hz": int(environment().get("LIVE_SPEECH_INPUT_SAMPLE_RATE", "16000")),
            "output_hz": int(environment().get("LIVE_SPEECH_OUTPUT_SAMPLE_RATE", "24000")),
        },
    }
