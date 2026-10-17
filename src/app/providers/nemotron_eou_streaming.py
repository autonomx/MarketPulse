"""Low-latency Nemotron ASR + Parakeet Realtime EOU runtime.

The 600M Nemotron model owns transcript text. The 120M Parakeet model is
observed only for its <EOU> token and never contributes transcript text.
Imports of torch/numpy/NeMo are deliberately lazy so launcher and CI imports do
not require the heavyweight speech environment.
"""
from __future__ import annotations
import logging

from app.config.env import environment

import json
import os
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

NEMOTRON_MODEL_NAME = "nvidia/nemotron-speech-streaming-en-0.6b"
EOU_MODEL_NAME = "nvidia/parakeet_realtime_eou_120m-v1"
SAMPLE_RATE = 16_000
EOU_TOKEN = "<EOU>"
EOB_TOKEN = "<EOB>"


def env_int(name: str, default: int) -> int:
    try:
        return int(environment().get(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def pcm16le_to_float32(pcm16le: bytes):
    if len(pcm16le) % 2:
        raise ValueError("pcm16_audio_must_contain_whole_samples")
    import numpy as np

    return np.frombuffer(pcm16le, dtype="<i2").astype(np.float32) / 32768.0


def strip_eou_control_tokens(text: str) -> str:
    return " ".join(text.replace(EOU_TOKEN, " ").replace(EOB_TOKEN, " ").split()).strip()


def has_eou_token(text: str) -> bool:
    return EOU_TOKEN in text


def has_meaningful_transcript(text: str) -> bool:
    """Return whether Nemotron has produced user speech worth ending."""
    return any(character.isalnum() for character in text)


def _metric(event: str, **fields: Any) -> None:
    logger.info("[STT_METRIC] "
        + json.dumps(
            {
                "event": event,
                "source": "nemotron-parakeet-eou-streaming",
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **fields,
            },
            sort_keys=True,
            default=str,
        ))


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        return _extract_text(value[0])
    text = getattr(value, "text", None)
    return text if isinstance(text, str) else str(value)


def _normalize_streaming_hypotheses(hypotheses: Any) -> Any:
    """Make RNNT hypotheses compatible with NeMo's continuation merger.

    Some NeMo decoders expose ``Hypothesis.timestamp`` as a mapping containing
    the token-level ``timestep`` list.  The cache-aware continuation path still
    calls ``Hypothesis.merge_`` from an older contract that expects that field
    itself to be list-like and calls ``.extend`` on it.  Live transcription only
    needs the token timesteps, so flatten that representation before the next
    streaming step.
    """

    if not isinstance(hypotheses, (list, tuple)):
        return hypotheses

    normalized = list(hypotheses)
    for hypothesis in normalized:
        timestamp = getattr(hypothesis, "timestamp", None)
        if not isinstance(timestamp, dict):
            continue
        timestep = timestamp.get("timestep", ())
        try:
            hypothesis.timestamp = list(timestep)
        except TypeError:
            hypothesis.timestamp = []
    return normalized


def _select_device(torch_module: Any, env_name: str, fallback: str) -> str:
    requested = environment().get(env_name, fallback).strip().lower()
    if requested == "auto":
        return "cuda" if torch_module.cuda.is_available() else "cpu"
    if requested.startswith("cuda") and not torch_module.cuda.is_available():
        return "cpu"
    return requested or "cpu"


def _configure_streaming_context(model: Any, right_context: int) -> None:
    encoder = getattr(model, "encoder", None)
    if encoder is None:
        raise RuntimeError("streaming_model_missing_encoder")
    setter = getattr(encoder, "set_default_att_context_size", None)
    if callable(setter):
        setter(att_context_size=[70, right_context])
    elif getattr(encoder, "streaming_cfg", None) is None:
        setup = getattr(encoder, "setup_streaming_params", None)
        if callable(setup):
            setup()


@dataclass(frozen=True)
class StreamingUpdate:
    transcript: str
    transcript_changed: bool
    eou: bool
    model_ms: float


class CacheAwareRnntStream:
    """One cache-aware RNNT utterance with independent encoder/decoder caches."""

    def __init__(self, model: Any, *, right_context: int, detect_eou: bool = False) -> None:
        import torch
        from nemo.collections.asr.parts.utils.streaming_utils import CacheAwareStreamingAudioBuffer

        _configure_streaming_context(model, right_context)
        self.model = model
        self.torch = torch
        self.detect_eou = detect_eou
        self.buffer = CacheAwareStreamingAudioBuffer(
            model=model,
            online_normalization=False,
            pad_and_drop_preencoded=False,
        )
        (
            self.cache_last_channel,
            self.cache_last_time,
            self.cache_last_channel_len,
        ) = model.encoder.get_initial_cache_state(batch_size=1)
        self.previous_hypotheses = None
        self.pred_out_stream = None
        self.step_num = 0
        self.stream_id = -1
        self.raw_text = ""
        self.transcript = ""
        self.eou_seen = False

    def feed_pcm16(self, pcm16le: bytes) -> StreamingUpdate:
        if not pcm16le:
            return StreamingUpdate(self.transcript, False, False, 0.0)
        started = time.perf_counter()
        audio = pcm16le_to_float32(pcm16le)
        if self.stream_id < 0:
            self.buffer.append_audio(audio, stream_id=-1)
            # CacheAwareStreamingAudioBuffer uses -1 to mean "new stream" and
            # leaves that sentinel unchanged on the initial append. Subsequent
            # appends must explicitly target stream zero.
            self.stream_id = 0
        else:
            self.buffer.append_audio(audio, stream_id=self.stream_id)
        before = self.transcript
        new_eou = False
        for chunk_audio, chunk_lengths in iter(self.buffer):
            raw = self._step(chunk_audio, chunk_lengths)
            if raw:
                self.raw_text = raw
                if self.detect_eou and has_eou_token(raw) and not self.eou_seen:
                    self.eou_seen = True
                    new_eou = True
                self.transcript = strip_eou_control_tokens(raw)
        return StreamingUpdate(
            transcript=self.transcript,
            transcript_changed=self.transcript != before,
            eou=new_eou,
            model_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _step(self, chunk_audio: Any, chunk_lengths: Any) -> str:
        torch = self.torch
        drop_extra = 0 if self.step_num == 0 else self.model.encoder.streaming_cfg.drop_extra_pre_encoded
        self.previous_hypotheses = _normalize_streaming_hypotheses(self.previous_hypotheses)
        with torch.inference_mode():
            (
                self.pred_out_stream,
                transcribed_texts,
                self.cache_last_channel,
                self.cache_last_time,
                self.cache_last_channel_len,
                self.previous_hypotheses,
            ) = self.model.conformer_stream_step(
                processed_signal=chunk_audio.to(torch.float32),
                processed_signal_length=chunk_lengths,
                cache_last_channel=self.cache_last_channel,
                cache_last_time=self.cache_last_time,
                cache_last_channel_len=self.cache_last_channel_len,
                keep_all_outputs=self.buffer.is_buffer_empty(),
                previous_hypotheses=self.previous_hypotheses,
                previous_pred_out=self.pred_out_stream,
                drop_extra_pre_encoded=drop_extra,
                return_transcription=True,
            )
        self.previous_hypotheses = _normalize_streaming_hypotheses(self.previous_hypotheses)
        self.step_num += 1
        return _extract_text(transcribed_texts)

    def finalize_text(self) -> str:
        return self.transcript.strip()


@dataclass
class HybridStream:
    nemotron: CacheAwareRnntStream
    eou: CacheAwareRnntStream
    last_partial: str = ""
    eou_rearm_count: int = 0
    ignored_pre_speech_eou_count: int = 0


@dataclass(frozen=True)
class HybridUpdate:
    transcript: str
    transcript_changed: bool
    eou: bool
    nemotron_ms: float
    eou_ms: float


class NemotronEouModelManager:
    """Owns shared models and per-segment cache-aware streaming state."""

    def __init__(self) -> None:
        self.nemotron_model: Any = None
        self.eou_model: Any = None
        self.nemotron_device = "unloaded"
        self.eou_device = "unloaded"
        self.nemotron_right_context = env_int("OMNIX_NEMOTRON_RIGHT_CONTEXT", 1)
        self.eou_right_context = env_int("OMNIX_EOU_RIGHT_CONTEXT", 1)
        self.feed_chunk_ms = max(80, env_int("OMNIX_STT_STREAM_CHUNK_MS", 160))
        self._streams: dict[str, HybridStream] = {}
        self._load_lock = threading.RLock()
        self._nemotron_lock = threading.RLock()
        self._eou_lock = threading.RLock()

    @property
    def loaded(self) -> bool:
        return self.nemotron_model is not None and self.eou_model is not None

    @property
    def feed_chunk_samples(self) -> int:
        return max(1, round(SAMPLE_RATE * self.feed_chunk_ms / 1000))

    def load(self) -> None:
        if self.loaded:
            return
        with self._load_lock:
            if self.loaded:
                return
            import torch
            from nemo.collections.asr.models import ASRModel

            fallback_device = environment().get("OMNIX_STT_DEVICE", "auto")
            self.nemotron_device = _select_device(torch, "OMNIX_NEMOTRON_DEVICE", fallback_device)
            self.eou_device = _select_device(torch, "OMNIX_EOU_DEVICE", fallback_device)
            nemotron_name = environment().get("OMNIX_NEMOTRON_MODEL", NEMOTRON_MODEL_NAME).strip()
            eou_name = environment().get("OMNIX_EOU_MODEL", EOU_MODEL_NAME).strip()
            logger.info(f"[STT] Loading authoritative Nemotron model {nemotron_name} on {self.nemotron_device}")
            self.nemotron_model = ASRModel.from_pretrained(model_name=nemotron_name)
            self.nemotron_model.to(self.nemotron_device)
            self.nemotron_model.eval()
            _configure_streaming_context(self.nemotron_model, self.nemotron_right_context)
            logger.info(f"[STT] Loading Parakeet Realtime EOU model {eou_name} on {self.eou_device}")
            self.eou_model = ASRModel.from_pretrained(model_name=eou_name)
            self.eou_model.to(self.eou_device)
            self.eou_model.eval()
            _configure_streaming_context(self.eou_model, self.eou_right_context)
            logger.info("[STT] Hybrid streaming ready: "
                f"Nemotron right_context={self.nemotron_right_context}, "
                f"EOU right_context={self.eou_right_context}, feed_chunk_ms={self.feed_chunk_ms}")

    def health_details(self) -> dict[str, Any]:
        return {
            "provider": "nemotron_parakeet_eou",
            "authoritative_transcript_model": environment().get("OMNIX_NEMOTRON_MODEL", NEMOTRON_MODEL_NAME),
            "endpoint_model": environment().get("OMNIX_EOU_MODEL", EOU_MODEL_NAME),
            "nemotron_device": self.nemotron_device,
            "eou_device": self.eou_device,
            "chunk_ms": self.feed_chunk_ms,
            "nemotron_right_context": self.nemotron_right_context,
            "eou_right_context": self.eou_right_context,
            "active_streams": len(self._streams),
        }

    def warm_streaming_runtime(self, duration_ms: int = 2_000) -> float:
        """Exercise both cache-aware streaming models before readiness is exposed."""

        self.load()
        segment_id = "__omnix_streaming_warmup__"
        chunk = b"\x00\x00" * self.feed_chunk_samples
        chunk_count = max(2, (max(1, duration_ms) + self.feed_chunk_ms - 1) // self.feed_chunk_ms)
        started = time.perf_counter()
        try:
            for _ in range(chunk_count):
                self.feed(segment_id, chunk)
        finally:
            self.release(segment_id)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        _metric(
            "stt_streaming_runtime_warmed",
            duration_ms=chunk_count * self.feed_chunk_ms,
            chunk_count=chunk_count,
            elapsed_ms=round(elapsed_ms, 3),
        )
        return elapsed_ms

    def _new_eou_stream(self) -> CacheAwareRnntStream:
        return CacheAwareRnntStream(
            self.eou_model,
            right_context=self.eou_right_context,
            detect_eou=True,
        )

    def ensure_stream(self, segment_id: str) -> HybridStream:
        self.load()
        existing = self._streams.get(segment_id)
        if existing is not None:
            return existing
        stream = HybridStream(
            nemotron=CacheAwareRnntStream(
                self.nemotron_model,
                right_context=self.nemotron_right_context,
                detect_eou=False,
            ),
            eou=self._new_eou_stream(),
        )
        self._streams[segment_id] = stream
        return stream

    def rearm_eou(self, segment_id: str) -> bool:
        """Reset only Parakeet EOU state while preserving Nemotron caches."""
        stream = self._streams.get(segment_id)
        if stream is None or self.eou_model is None:
            return False
        with self._eou_lock:
            stream.eou = self._new_eou_stream()
            stream.eou_rearm_count += 1
        return True

    def feed(self, segment_id: str, pcm16le: bytes) -> HybridUpdate:
        stream = self.ensure_stream(segment_id)
        # Run Nemotron first so the authoritative partial is current by the time
        # an EOU event is surfaced to the browser.
        with self._nemotron_lock:
            nemotron = stream.nemotron.feed_pcm16(pcm16le)
        with self._eou_lock:
            eou = stream.eou.feed_pcm16(pcm16le)
        stream.last_partial = nemotron.transcript

        eou_event = eou.eou
        if eou_event:
            meaningful_speech = has_meaningful_transcript(nemotron.transcript)
            # Parakeet's <EOU> is an utterance boundary for its own RNNT state.
            # Always replace that endpoint stream after a token so a rejected
            # early/mid-turn candidate cannot permanently consume the only EOU
            # available for this browser segment. Nemotron is deliberately left
            # untouched and remains the authoritative continuous transcript.
            rearmed = self.rearm_eou(segment_id)
            _metric(
                "stt_eou_rearmed",
                segment_id=segment_id,
                transcript_chars=len(nemotron.transcript),
                meaningful_speech=meaningful_speech,
                rearmed=rearmed,
                rearm_count=stream.eou_rearm_count,
            )
            if not meaningful_speech:
                stream.ignored_pre_speech_eou_count += 1
                eou_event = False
                _metric(
                    "stt_eou_ignored_pre_speech",
                    segment_id=segment_id,
                    ignored_count=stream.ignored_pre_speech_eou_count,
                    rearm_count=stream.eou_rearm_count,
                )

        return HybridUpdate(
            transcript=nemotron.transcript,
            transcript_changed=nemotron.transcript_changed,
            eou=eou_event,
            nemotron_ms=nemotron.model_ms,
            eou_ms=eou.model_ms,
        )

    def finalize(self, segment_id: str, pcm16le_fallback: bytes = b"") -> tuple[str, dict[str, float]]:
        stream = self._streams.get(segment_id)
        if stream is not None:
            text = stream.nemotron.finalize_text()
            if text:
                return text, {"streaming_final": 1.0, "offline_fallback": 0.0}
        if not pcm16le_fallback:
            return "", {"streaming_final": 0.0, "offline_fallback": 0.0}
        started = time.perf_counter()
        text = self.transcribe_pcm16(pcm16le_fallback)
        return text, {
            "streaming_final": 0.0,
            "offline_fallback": 1.0,
            "offline_fallback_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    def release(self, segment_id: str) -> None:
        self._streams.pop(segment_id, None)

    def transcribe_pcm16(self, pcm16le: bytes) -> str:
        self.load()
        if not pcm16le:
            return ""
        fd, path_value = tempfile.mkstemp(prefix="omnix-nemotron-", suffix=".wav")
        os.close(fd)
        path = Path(path_value)
        try:
            with wave.open(str(path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(SAMPLE_RATE)
                wav_file.writeframes(pcm16le)
            with self._nemotron_lock:
                output = self.nemotron_model.transcribe([str(path)])
            return strip_eou_control_tokens(_extract_text(output))
        finally:
            path.unlink(missing_ok=True)


model_manager = NemotronEouModelManager()
