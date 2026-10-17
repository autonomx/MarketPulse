"""Control-plane calls to the TTS model service (health, speakers, voice cloning).

Audio comes from the service as streamed PCM16 through
``app.providers.qwen_http_gateway``; nothing here moves base64 audio (WP-7.3).
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.runtime.http_client import PooledHttpClient, shared_http_client
from app.security.service_token import service_headers

from app.providers.voice_debug import voice_debug_log, voice_debug_log_path


def _normalize_base_url(value: str | None, default: str) -> str:
    raw = (value or default).strip().strip('"').strip("'")
    raw = raw.replace(" ", "")
    return raw.rstrip("/")


def _tts_base_url() -> str:
    from app.runtime.config import get_runtime_config
    endpoint = get_runtime_config().tts
    return endpoint.url if endpoint else 'http://127.0.0.1:5101'


def _http() -> PooledHttpClient:
    """The pooled client for the TTS model service (WP-7.2)."""
    return shared_http_client("tts-service")


def _trace_id(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4()}"


def tts_health(timeout: float = 5.0) -> dict[str, Any]:
    try:
        response = _http().get(f"{_tts_base_url()}/health", timeout=timeout, retry=False)
        response.raise_for_status()
        data = response.json()
        data["reachable"] = True
        return data
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "ok": False,
            "reachable": False,
            "error": str(exc),
            "provider": "tts-http",
        }


def tts_speakers(timeout: float = 10.0) -> dict[str, Any]:
    trace_id = _trace_id("tts-speakers")
    endpoint = f"{_tts_base_url()}/api/tts/speakers"
    voice_debug_log(
        "backend",
        "tts_speakers_request",
        trace_id=trace_id,
        endpoint=endpoint,
        log_path=voice_debug_log_path("backend"),
    )
    try:
        response = _http().get(endpoint, timeout=timeout, headers=service_headers())
        voice_debug_log(
            "backend",
            "tts_speakers_response",
            trace_id=trace_id,
            status_code=response.status_code,
            content_type=response.headers.get("content-type", ""),
            response_bytes=len(response.content),
        )
        response.raise_for_status()
        data = response.json()
        speakers = data.get("speakers") if isinstance(data, dict) else None
        voice_debug_log(
            "backend",
            "tts_speakers_decoded",
            trace_id=trace_id,
            speaker_ids=[
                str(row.get("id") or row.get("name") or "")
                for row in speakers
                if isinstance(row, dict)
            ][:100] if isinstance(speakers, list) else [],
        )
        return data
    except (httpx.HTTPError, ValueError) as exc:
        voice_debug_log(
            "backend",
            "tts_speakers_failed",
            trace_id=trace_id,
            error=exc,
        )
        return {
            "success": False,
            "speakers": [],
            "provider": "tts-http",
            "error": str(exc),
            "reachable": False,
        }


def tts_voice_clone(
    *,
    voice_id: str,
    gender: str = "neutral",
    language: str = "en",
    ref_text: str = "",
    audio_bytes: bytes | None = None,
    filename: str = "voice.wav",
    timeout: float = 120.0,
) -> dict[str, Any]:
    data = {
        "voice_id": voice_id,
        "gender": gender,
        "language": language,
        "ref_text": ref_text,
    }
    files = None
    if audio_bytes:
        files = {
            "file": (filename, audio_bytes, "audio/wav"),
        }
    response = _http().post(
        f"{_tts_base_url()}/api/tts/voice_clone",
        data=data,
        files=files,
        timeout=timeout,
        headers=service_headers(),
    )
    response.raise_for_status()
    return response.json()
