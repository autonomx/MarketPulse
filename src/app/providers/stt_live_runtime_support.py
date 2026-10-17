"""Inference and diagnostics support for low-latency Parakeet live STT."""
from __future__ import annotations
import logging

from app.config.env import environment

import json
import math
import tempfile
import threading
import time
from array import array
from pathlib import Path
from typing import Any

from app.providers.stt_streaming_audio import DEFAULT_SAMPLE_RATE, write_pcm16_wav

logger = logging.getLogger(__name__)

_TRANSCRIBE_LOCK = threading.Lock()
_WARMED = False


def env_flag(name: str, default: str = "1") -> bool:
    return environment().get(name, default).strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(name: str, default: float) -> float:
    try:
        return float(environment().get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(environment().get(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def metric(event: str, **fields: Any) -> None:
    payload = {
        "event": event,
        "source": "parakeet-live-runtime",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **fields,
    }
    logger.info("[STT_METRIC] " + json.dumps(payload, sort_keys=True, default=str))


def extract_text(output: Any) -> str:
    if isinstance(output, tuple) and output:
        output = output[0]
    if isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, str):
            return first.strip()
        text = getattr(first, "text", None)
        if isinstance(text, str):
            return text.strip()
    if isinstance(output, str):
        return output.strip()
    text = getattr(output, "text", None)
    return text.strip() if isinstance(text, str) else ""


def transcribe_path(model: Any, audio_path: Path) -> tuple[str, float]:
    if model is None:
        raise RuntimeError("ASR model not loaded")
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - production dependency guard.
        raise RuntimeError("PyTorch is required for Parakeet inference") from exc
    started_at = time.perf_counter()
    with _TRANSCRIBE_LOCK, torch.inference_mode():
        try:
            output = model.transcribe(paths2audio_files=[str(audio_path)])
        except TypeError:
            try:
                output = model.transcribe([str(audio_path)])
            except Exception:
                output = model.transcribe([str(audio_path)], timestamps=True)
    return extract_text(output), (time.perf_counter() - started_at) * 1000.0


def warmed() -> bool:
    return _WARMED


def warm_model(model: Any, *, device: str) -> None:
    global _WARMED
    if _WARMED or model is None or not env_flag("PARAKEET_WARMUP", "1"):
        return
    started_at = time.perf_counter()
    warmup_seconds = max(0.25, env_float("PARAKEET_WARMUP_SECONDS", 0.75))
    sample_count = max(1, round(DEFAULT_SAMPLE_RATE * warmup_seconds))
    samples = array(
        "h",
        (
            round(450 * math.sin(2.0 * math.pi * 220.0 * index / DEFAULT_SAMPLE_RATE))
            for index in range(sample_count)
        ),
    )
    with tempfile.TemporaryDirectory(prefix="omnix_stt_warmup_") as temp_dir:
        path = write_pcm16_wav(Path(temp_dir) / "warmup.wav", samples.tobytes())
        try:
            _, inference_ms = transcribe_path(model, path)
        except Exception as exc:
            metric(
                "stt_model_warmup_failed",
                elapsed_ms=round((time.perf_counter() - started_at) * 1000.0, 3),
                error_type=type(exc).__name__,
            )
            return
    _WARMED = True
    metric(
        "stt_model_warmup_completed",
        elapsed_ms=round((time.perf_counter() - started_at) * 1000.0, 3),
        inference_ms=round(inference_ms, 3),
        device=device,
        warmup_audio_ms=round(warmup_seconds * 1000.0, 3),
    )
