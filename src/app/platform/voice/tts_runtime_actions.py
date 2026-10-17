"""Warm and unload actions for the local chat TTS runtime."""
from __future__ import annotations

import logging

import time
from typing import Any

from app.providers.service import get_tts_provider

from .tts_engines import TRANSCRIPT_ENGINES
from .tts_runtime_state import (
    STATE,
    STATE_LOCK,
    WARMUP_STREAM_ID,
    configured_speaker,
    snapshot,
    utc_now,
)
from app.observability.tts_stream_diagnostics import stream_log

logger = logging.getLogger(__name__)


def _reset_cached_model() -> None:
    from app.providers.vendor.qwen3_tts import reset_tts_model_cache

    reset_tts_model_cache()


def _has_transcript(speaker: str) -> bool:
    from app.assets.voice_clone_identity import reference_transcript
    from app.providers.voice_references import clone_reference

    path, transcript = clone_reference(speaker)
    return bool(path and (transcript or reference_transcript(path)))


def _select_speaker(provider: Any) -> str | None:
    configured = configured_speaker()
    if configured:
        return configured
    try:
        speakers = provider.get_speakers()
    except Exception:
        logger.debug("suppressed error in %s", "_select_speaker", exc_info=True)
        return None
    candidates = [
        value for value in (
            str(item.get("id") or "").strip() if isinstance(item, dict) else ""
            for item in (speakers if isinstance(speakers, list) else [])
        )
        if value and value != "default"
    ]
    if getattr(provider, "provider_name", None) in TRANSCRIPT_ENGINES:
        # These engines cannot clone a sample without its words.
        return next((value for value in candidates if _has_transcript(value)), candidates[0] if candidates else None)
    return candidates[0] if candidates else None


def warm_tts_runtime(trigger: str = "manual") -> dict[str, Any]:
    with STATE_LOCK:
        if STATE["status"] == "warming":
            already_warming = True
        else:
            already_warming = False
            STATE.update(
                status="warming",
                trigger=trigger,
                started_at=utc_now(),
                completed_at=None,
                duration_ms=None,
                graph_warmed=False,
                first_chunk_samples=None,
                sample_rate=None,
                error=None,
            )
    if already_warming:
        return snapshot()

    started = time.perf_counter()
    provider: Any | None = None
    iterator: Any | None = None
    stream_log(WARMUP_STREAM_ID, "lifecycle", "warmup_started", trigger=trigger)
    try:
        provider = get_tts_provider()
        if provider is None:
            raise RuntimeError("tts_provider_unavailable")
        provider_class = f"{type(provider).__module__}.{type(provider).__qualname__}"
        provider_name = getattr(provider, "provider_name", None)
        with STATE_LOCK:
            STATE.update(provider_class=provider_class, provider_name=provider_name)

        result = provider.start() if hasattr(provider, "start") else {"running": True}
        if isinstance(result, dict) and not result.get("running"):
            raise RuntimeError(str(result.get("error") or result.get("message") or "tts_provider_start_failed"))

        speaker = _select_speaker(provider)
        with STATE_LOCK:
            STATE.update(speaker=speaker, model_loaded=True)
        iterator = provider.generate_audio_stream(
            text="Audio runtime ready.",
            speaker=speaker,
            language="English",
            chunk_size=8,
            temperature=0.6,
            top_k=20,
            top_p=0.85,
            repetition_penalty=1.0,
            append_silence=False,
            max_new_tokens=192,
            non_streaming_mode=False,
            parity_mode=False,
        )
        audio, sample_rate, timing = next(iterator)
        samples = len(audio) if hasattr(audio, "__len__") else None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.synchronize()
        except Exception:
            logger.debug("suppressed error in %s", "warm_tts_runtime", exc_info=True)

        elapsed = round((time.perf_counter() - started) * 1000, 3)
        with STATE_LOCK:
            STATE.update(
                status="ready",
                completed_at=utc_now(),
                duration_ms=elapsed,
                model_loaded=True,
                graph_warmed=True,
                first_chunk_samples=int(samples) if samples is not None else None,
                sample_rate=int(sample_rate or 0) or None,
                warmup_count=int(STATE["warmup_count"]) + 1,
            )
        stream_log(
            WARMUP_STREAM_ID,
            "lifecycle",
            "warmup_completed",
            trigger=trigger,
            elapsed_ms=elapsed,
            provider_class=provider_class,
            provider_name=provider_name,
            speaker=speaker,
            first_chunk_samples=samples,
            sample_rate=sample_rate,
            provider_timing=timing,
        )
    except Exception as exc:
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        loaded = False
        if provider is not None and hasattr(provider, "get_runtime_status"):
            try:
                loaded = bool(provider.get_runtime_status().get("model_loaded"))
            except Exception:
                logger.debug("suppressed error in %s", "warm_tts_runtime", exc_info=True)
        with STATE_LOCK:
            STATE.update(status="failed", completed_at=utc_now(), duration_ms=elapsed, model_loaded=loaded, error=str(exc))
        stream_log(WARMUP_STREAM_ID, "lifecycle", "warmup_failed", trigger=trigger, elapsed_ms=elapsed, error=repr(exc))
    finally:
        if iterator is not None and hasattr(iterator, "close"):
            try:
                iterator.close()
            except Exception:
                logger.debug("suppressed error in %s", "warm_tts_runtime", exc_info=True)
    return snapshot(provider)


def unload_tts_runtime(trigger: str = "manual") -> dict[str, Any]:
    provider: Any | None = None
    stream_log(WARMUP_STREAM_ID, "lifecycle", "unload_started", trigger=trigger)
    try:
        provider = get_tts_provider()
        if provider is None or not hasattr(provider, "stop") or not provider.stop():
            raise RuntimeError("tts_provider_unload_failed")
        _reset_cached_model()
        with STATE_LOCK:
            STATE.update(
                status="unloaded",
                trigger=trigger,
                completed_at=utc_now(),
                model_loaded=False,
                graph_warmed=False,
                error=None,
                unload_count=int(STATE["unload_count"]) + 1,
            )
        stream_log(WARMUP_STREAM_ID, "lifecycle", "unload_completed", trigger=trigger)
    except Exception as exc:
        with STATE_LOCK:
            STATE.update(status="failed", error=str(exc))
        stream_log(WARMUP_STREAM_ID, "lifecycle", "unload_failed", trigger=trigger, error=repr(exc))
    return snapshot(provider)
