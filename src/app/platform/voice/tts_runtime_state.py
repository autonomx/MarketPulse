"""Shared state for local TTS model readiness."""
from __future__ import annotations

from app.config.env import env_str, environment

import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any

from app.settings.access import load_settings

WARMUP_STREAM_ID = "tts-runtime-warmup"
STARTUP_WARMUP_ENV = "OMNIX_TTS_STARTUP_WARMUP"
WARMUP_SPEAKER_ENV = "OMNIX_TTS_WARMUP_SPEAKER"
STATE_LOCK = threading.Lock()
STATE: dict[str, Any] = {
    "status": "idle",
    "trigger": None,
    "provider_class": None,
    "provider_name": None,
    "speaker": None,
    "started_at": None,
    "completed_at": None,
    "duration_ms": None,
    "model_loaded": False,
    "graph_warmed": False,
    "first_chunk_samples": None,
    "sample_rate": None,
    "error": None,
    "warmup_count": 0,
    "unload_count": 0,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def provider_settings() -> dict[str, Any]:
    try:
        settings = load_settings()
        name = str(settings.get("audio_provider_tts") or "faster-qwen3-tts")
        value = settings.get(name)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def startup_warmup_enabled() -> bool:
    raw = environment().get(STARTUP_WARMUP_ENV)
    if raw is not None:
        return raw.strip().casefold() not in {"0", "false", "no", "off", "disabled"}
    if "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in environment():
        return False
    return bool(provider_settings().get("startup_warmup", True))


def configured_speaker() -> str | None:
    value = environment().get(WARMUP_SPEAKER_ENV)
    if value is None:
        value = str(provider_settings().get("warmup_speaker") or "")
    value = value.strip()
    return value or None


def snapshot(provider: Any | None = None) -> dict[str, Any]:
    with STATE_LOCK:
        payload = dict(STATE)
    payload.update(
        startup_warmup_enabled=startup_warmup_enabled(),
        startup_warmup_env=STARTUP_WARMUP_ENV,
        warmup_speaker_env=WARMUP_SPEAKER_ENV,
    )
    if provider is not None and hasattr(provider, "get_runtime_status"):
        try:
            payload["provider_runtime"] = provider.get_runtime_status()
        except Exception as exc:
            payload["provider_runtime"] = {"status_error": str(exc)}
    return payload
