"""
Faster Qwen3 TTS Provider Plugin

Implements the BaseTTSProvider interface for the faster-qwen3-tts library
with CUDA graph acceleration for real-time voice cloning.
"""
from app.config.env import env_str as _env_str

import base64
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Union

import numpy as np

from app.runtime.paths import VOICE_CLONES_DIR
from .audio_base import (
    AudioProviderCapability,
    BaseTTSProvider,
)
from .tts_priority import generation_slot
from .voice_references import clone_reference as _clone_reference
from .voice_references import clone_speakers
from .vendor.qwen3_tts.loader import _resolve_model_source
from .vendor.qwen3_tts import (
    ensure_vendored_qwen3_tts_available,
    get_or_create_tts_model,
    reset_tts_model_cache,
)

logger = logging.getLogger(__name__)

# Fallback preview audio aims to stay short, deterministic, and obviously synthetic.
# We size it using a rough conversational reading rate so longer prompts yield slightly
# longer previews, and use a simple A3/E4 interval to produce a stable tone when no
# reference clip can be read.
FALLBACK_CHARACTERS_PER_SECOND = 14.0
FALLBACK_CARRIER_FREQ_HZ = 220.0
FALLBACK_HARMONIC_FREQ_HZ = 330.0


DEFAULT_QWEN3_TTS_MODEL_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"


def _clone_conditioning(kwargs: dict[str, Any], model_config: dict[str, Any], saved_text: str) -> tuple[str, bool]:
    ref_text = str(kwargs.get("ref_text") or saved_text).strip()
    xvec_only = kwargs.get("xvec_only")
    if xvec_only is None:
        xvec_only = False if ref_text else model_config.get("xvec_only", True)
    return ref_text, bool(xvec_only)


def _model_artifact_signature(model_name: str) -> tuple[object, ...]:
    """Track local model files so a cached model cannot outlive changed weights."""
    root = Path(_resolve_model_source(model_name)).expanduser()
    if not root.is_dir():
        return (model_name,)
    files = sorted(path for path in root.rglob("*") if path.is_file()
                   and path.suffix.lower() in {".safetensors", ".json", ".txt", ".model"})
    return (str(root.resolve()), tuple(
        (path.relative_to(root).as_posix(), path.stat().st_size,
         path.stat().st_mtime_ns) for path in files))


def _resolve_qwen3_model_name(config: Optional[Dict[str, Any]] = None) -> str:
    """
    Resolve the model source for Qwen3-TTS.

    Important distinction:
      - resources/models/tts/faster-qwen3-tts-main = vendored runtime code package
      - model_name / model_dir = actual model weights source

    Resolution order:
      1. explicit env override (OMNIX_TTS_MODEL_DIR / OMNIX_QWEN3_TTS_MODEL_DIR)
      2. explicit config model_dir
      3. explicit config model_name, unless it is the legacy broken local-path default
      4. canonical HF repo id default
    """
    config = config or {}

    env_model_dir = (_env_str("OMNIX_TTS_MODEL_DIR", "") or "").strip()
    if env_model_dir:
        return env_model_dir

    env_qwen3_model_dir = (_env_str("OMNIX_QWEN3_TTS_MODEL_DIR", "") or "").strip()
    if env_qwen3_model_dir:
        return env_qwen3_model_dir

    config_model_dir = str(config.get("model_dir") or "").strip()
    if config_model_dir:
        return config_model_dir

    config_model_name = str(config.get("model_name") or "").strip()
    legacy_broken_defaults = {
        r"F:\LLM\omnix\Qwen\Qwen3-TTS-12Hz-0.6B-Base",
        r".\Qwen\Qwen3-TTS-12Hz-0.6B-Base",
        r"Qwen\Qwen3-TTS-12Hz-0.6B-Base",
    }
    if config_model_name and config_model_name not in legacy_broken_defaults:
        return config_model_name

    return DEFAULT_QWEN3_TTS_MODEL_REPO


def _is_cuda_graph_generation_error(error: BaseException) -> bool:
    message = str(error).casefold()
    return (
        "graph capture" in message
        or "cuda graph" in message
        or "during capture" in message
        or "stream is capturing" in message
        or "outside graph" in message
        or "offset increment" in message
    )


def _is_retryable_generation_error(error: BaseException) -> bool:
    message = str(error).casefold()
    return (
        _is_cuda_graph_generation_error(error)
        or ("size of tensor" in message and "must match the size of tensor" in message)
    )


def _reset_cached_model_after_generation_error(error: BaseException) -> None:
    global _model_loader

    with _model_loader.lock:
        _model_loader.model = None
        _model_loader.initialized = False
        _model_loader.last_error = str(error)
        _model_loader.last_error_type = type(error).__name__
        _model_loader.last_error_at = datetime.now(timezone.utc).isoformat()
        try:
            reset_tts_model_cache()
        except Exception as reset_exc:
            logger.warning("Failed to reset FasterQwen3TTS model cache after generation error: %s", reset_exc)


def _disable_cuda_graphs_for_retry(model: Any) -> None:
    """Force eager decoding after the model's CUDA graph has proven unusable."""
    if hasattr(model, "_cuda_graphs_enabled"):
        model._cuda_graphs_enabled = False


def _decode_in_parity_mode(model: Any, kwargs: dict[str, Any], config: dict[str, Any]) -> bool:
    """Whether this request decodes without CUDA graphs.

    ``use_cuda_graphs=False`` is a per-request choice: it must not switch graphs off on the
    shared model, or every later fast request replays a graph that was never captured.
    A model whose graphs were disabled after a failure only decodes in parity mode.
    """
    requested = kwargs.get("parity_mode", config.get("parity_mode", True))
    return bool(requested) or kwargs.get("use_cuda_graphs") is False or not getattr(model, "_cuda_graphs_enabled", True)


def _generate_audio_with_streaming_fallback(model: Any, gen_kwargs: dict[str, Any]) -> tuple[list[Any], int]:
    stream_kwargs = dict(gen_kwargs)
    stream_kwargs.pop("progress_callback", None)
    stream_kwargs["parity_mode"] = True
    stream_kwargs["non_streaming_mode"] = False

    chunks: list[np.ndarray] = []
    sample_rate = 0
    for audio_chunk, sr, _timing in model.generate_voice_clone_streaming(**stream_kwargs):
        if not _is_valid_audio(audio_chunk):
            continue
        chunks.append(np.asarray(audio_chunk, dtype=np.float32))
        sample_rate = int(sr or sample_rate or 24_000)

    if not chunks:
        return [], sample_rate or 24_000
    return [np.concatenate(chunks)], sample_rate or 24_000


# ---------------------------------------------------------------------------
# Audio hardening helpers  (Issue 3 – prevent stream corruption)
# ---------------------------------------------------------------------------

def _is_valid_audio(audio: np.ndarray) -> bool:
    """Return *True* if *audio* is a usable waveform array."""
    if audio is None or len(audio) == 0:
        return False
    if np.isnan(audio).any():
        return False
    # Silence bug – all-zero / near-zero output
    if np.max(np.abs(audio)) < 1e-5:
        return False
    # Explosion bug – values far outside normal range
    if np.max(np.abs(audio)) > 5:
        return False
    return True


def soft_clip(x: np.ndarray) -> np.ndarray:
    """Gentle soft limiter: ``x / (1 + |x|)``.

    Smoothly saturates near ±1 while preserving more dynamic range than
    ``np.tanh``.  Unlike hard clipping (``np.clip``) it never introduces
    discontinuities.
    """
    return x / (1.0 + np.abs(x))


def find_best_offset(prev: np.ndarray, curr: np.ndarray,
                     max_shift: int = 128) -> int:
    """Find the sample offset in *curr* that best aligns with *prev*.

    TTS chunks often start at arbitrary waveform phase, causing micro
    discontinuities even with a raised-cosine crossfade.  This helper
    slides *curr* by up to *max_shift* samples and returns the offset
    that minimises the mean-squared difference against the tail of
    *prev*, producing a smoother splice.
    """
    if prev is None or curr is None or len(prev) == 0 or len(curr) == 0:
        return 0

    best_offset = 0
    best_score = float('inf')

    for shift in range(max_shift):
        overlap = min(len(prev), len(curr) - shift)
        if overlap <= 0:
            continue

        diff = prev[-overlap:] - curr[shift:shift + overlap]
        score = np.mean(diff ** 2)

        if score < best_score:
            best_score = score
            best_offset = shift

    return best_offset


def _normalize_audio(audio: np.ndarray) -> bytes:
    """Normalise a float waveform to 16-bit PCM bytes.

    Uses peak-based normalisation to avoid amplifying noise when the
    signal is already within range.  Only scales down when the peak
    exceeds 0.95 (close to clipping).  Final limiting uses
    :func:`soft_clip` for a smooth curve instead of hard clipping.
    """
    audio = audio.astype(np.float32)
    peak = np.max(np.abs(audio)) if len(audio) > 0 else 0.0
    if peak > 0.95:
        audio = audio / peak
    # Soft limiter — smoothly saturates near ±1, preserving more
    # dynamic range than tanh while still preventing hard clipping.
    audio = soft_clip(audio)
    audio_int16 = (audio * 32767).astype(np.int16)
    return audio_int16.tobytes()


def _align_bytes(audio_bytes: bytes) -> bytes:
    """Ensure *audio_bytes* length is a multiple of 2 (16-bit frame boundary)."""
    remainder = len(audio_bytes) % 2
    if remainder != 0:
        audio_bytes = audio_bytes[:-remainder]
    return audio_bytes


def crossfade_audio(prev: np.ndarray, curr: np.ndarray,
                    fade_samples: int = 512) -> np.ndarray:
    """Crossfade *prev* into *curr* over *fade_samples* using a raised-cosine curve.

    If either array is too short for a full crossfade the arrays are simply
    concatenated so no samples are lost.
    """
    if prev is None or len(prev) < fade_samples or len(curr) < fade_samples:
        if prev is not None:
            return np.concatenate([prev, curr]) if curr is not None else prev.copy()
        return curr if curr is not None else np.array([], dtype=np.float32)

    t = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    fade_in = 0.5 * (1.0 - np.cos(np.pi * t))   # raised-cosine
    fade_out = 1.0 - fade_in

    prev_tail = prev[-fade_samples:] * fade_out
    curr_head = curr[:fade_samples] * fade_in

    blended = prev_tail + curr_head

    return np.concatenate([
        prev[:-fade_samples],
        blended,
        curr[fade_samples:],
    ])


def apply_fade(audio: np.ndarray, fade_samples: int = 256) -> np.ndarray:
    """Apply a micro fade-in / fade-out to *audio* to eliminate edge clicks.

    Uses a raised-cosine ramp for perceptually smooth transitions.
    Returns a *copy* so the caller's original array is not mutated.
    """
    if audio is None or len(audio) < 2 * fade_samples:
        return audio.copy() if audio is not None else np.array([], dtype=np.float32)

    audio = audio.copy()
    n = min(fade_samples, len(audio) // 2)
    t = np.linspace(0.0, 1.0, n, dtype=np.float32)
    ramp = 0.5 * (1.0 - np.cos(np.pi * t))   # raised-cosine ramp
    audio[:n] *= ramp
    audio[-n:] *= ramp[::-1]
    return audio


def silence_pad(audio: np.ndarray, sample_rate: int,
                duration_sec: float = 0.05) -> np.ndarray:
    """Append a short silence to *audio* to guard against abrupt transitions."""
    silence = np.zeros(int(duration_sec * sample_rate), dtype=np.float32)
    return np.concatenate([audio, silence])


@dataclass
class ModelLoader:
    """Helper class to manage model loading state."""
    model: Any = None
    device: str = "cuda"
    initialized: bool = False
    artifact_signature: tuple[object, ...] | None = None
    last_error: str = ""
    last_error_type: str = ""
    last_error_at: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


# Global model loader singleton
_model_loader = ModelLoader()


class FasterQwen3TTSProvider(BaseTTSProvider):
    """
    TTS provider using FasterQwen3TTS with CUDA graphs for real-time performance.
    
    Supports voice cloning with reference audio and multilingual synthesis.
    """
    
    provider_name = "faster-qwen3-tts"
    provider_display_name = "Faster Qwen3 TTS"
    provider_description = "Real-time voice cloning TTS with CUDA graph acceleration (6-10x speedup)"
    generation_strategy_revision = "faster-qwen3-tts-generation-v4"

    @staticmethod
    def local_model_artifacts():
        """The installed model directory that audiobook renders are pinned to."""
        from app.settings.access import load_settings

        from .tts_artifacts import LocalArtifactsUnavailable, LocalModelArtifacts

        settings = load_settings().get("faster-qwen3-tts", {})
        source = _resolve_model_source(_resolve_qwen3_model_name(settings))
        directory = Path(source).expanduser().resolve()
        if not directory.is_dir():
            raise LocalArtifactsUnavailable("a local, pinned FasterQwen model directory is required")
        if not list(directory.glob("*.safetensors")):
            raise LocalArtifactsUnavailable("the configured FasterQwen model has no weight files")
        return LocalModelArtifacts(model_id="Qwen3-TTS", directory=directory)

    default_capabilities = [
        AudioProviderCapability.STREAMING,
        AudioProviderCapability.VOICE_CLONING,
        AudioProviderCapability.MULTILINGUAL,
        AudioProviderCapability.REAL_TIME,
    ]
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config or {})
        self.config = config or {}
        self._runtime_code_dir = (
            Path(__file__).parent.parent.parent / "resources" / "models" / "tts" / "faster-qwen3-tts-main"
        )
        resolved_model_name = _resolve_qwen3_model_name(self.config)
        self._sample_rate = int(self.config.get("sample_rate", 12000))
        self._model_instance = None
        self._model_config = dict(self.config)
        self._model_config["model_name"] = resolved_model_name
        self._validate_config()

        logger.info("FasterQwen3TTS runtime code dir: %s", self._runtime_code_dir)
        logger.info("FasterQwen3TTS configured model source: %s", self._model_config["model_name"])
        
    def _validate_config(self):
        """Validate and set defaults for provider configuration."""
        resolved_model_name = _resolve_qwen3_model_name(self._model_config)
        self._model_config["model_name"] = resolved_model_name

        self.device = self._model_config.get("device", "cuda")
        self.dtype = self._model_config.get("dtype", "bfloat16")
        self.max_seq_len = self._model_config.get("max_seq_len", 2048)

        # Keep normalized values mirrored back into model config so downstream code
        # never sees the stale legacy path.
        self._model_config["device"] = self.device
        self._model_config["dtype"] = self.dtype
        self._model_config["max_seq_len"] = self.max_seq_len

        logger.info(f"FasterQwen3TTS configured: model={self._model_config['model_name']}, device={self.device}")

    def resolve_generation_parameters(self, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Resolve sparse caller overrides into the complete cache/provenance identity."""
        requested = dict(parameters or {})
        return {
            "ref_text": requested.get("ref_text", ""),
            "max_new_tokens": requested.get("max_new_tokens", self.max_seq_len),
            "min_new_tokens": requested.get("min_new_tokens", 2),
            "temperature": requested.get("temperature", self._model_config.get("temperature", 0.9)),
            "top_k": requested.get("top_k", self._model_config.get("top_k", 50)),
            "top_p": requested.get("top_p", self._model_config.get("top_p", 1.0)),
            "do_sample": requested.get("do_sample", self._model_config.get("do_sample", True)),
            "repetition_penalty": requested.get(
                "repetition_penalty", self._model_config.get("repetition_penalty", 1.05)
            ),
            # None preserves automatic transcript-aware conditioning. The
            # selected voice revision binds the transcript into render identity.
            "xvec_only": requested.get("xvec_only"),
            "non_streaming_mode": requested.get(
                "non_streaming_mode", self._model_config.get("non_streaming_mode", True)
            ),
            "append_silence": requested.get(
                "append_silence", self._model_config.get("append_silence", True)
            ),
            "parity_mode": requested.get("parity_mode", self._model_config.get("parity_mode", True)),
            "use_cuda_graphs": requested.get("use_cuda_graphs", True),
            "_generation_strategy_revision": self.generation_strategy_revision,
        }
    
    def _get_model(self):
        """
        Get or initialize the FasterQwen3TTS model instance.
        Uses singleton pattern with thread-safe lazy initialization.
        """
        global _model_loader
        signature = _model_artifact_signature(self._model_config["model_name"])
        
        # Fast path: already initialized
        if (_model_loader.model is not None and _model_loader.initialized
                and _model_loader.artifact_signature == signature):
            return _model_loader.model
        
        # Thread-safe initialization
        with _model_loader.lock:
            # Double-check pattern
            if (_model_loader.model is not None and _model_loader.initialized
                    and _model_loader.artifact_signature == signature):
                return _model_loader.model
            if _model_loader.model is not None and _model_loader.initialized:
                _model_loader.model = None
                _model_loader.initialized = False
                _model_loader.artifact_signature = None
                reset_tts_model_cache()
            
            try:
                logger.info(
                    "Loading FasterQwen3TTS model '%s' on device '%s' "
                    "(runtime_code_dir=%s)",
                    self._model_config["model_name"],
                    self.device,
                    self._runtime_code_dir,
                )
                
                ensure_vendored_qwen3_tts_available()
                
                # Load vendored TTS model
                model_name = self._model_config['model_name']

                model = get_or_create_tts_model(
                    model_name=model_name,
                    device=self.device,
                    dtype=self.dtype,
                    max_seq_len=self.max_seq_len
                )
                if _model_artifact_signature(model_name) != signature:
                    reset_tts_model_cache()
                    raise RuntimeError("TTS model artifacts changed during model loading")
                
                _model_loader.model = model
                _model_loader.artifact_signature = signature
                _model_loader.last_error = ""
                _model_loader.last_error_type = ""
                _model_loader.last_error_at = ""
                _model_loader.initialized = True
                logger.info("FasterQwen3TTS model loaded successfully")
                
                return model
                
            except ImportError as e:
                logger.error(f"Failed to import faster_qwen3_tts: {e}")
                raise
            except Exception as e:
                _model_loader.model = None
                _model_loader.initialized = False
                _model_loader.artifact_signature = None
                _model_loader.last_error = str(e)
                _model_loader.last_error_type = type(e).__name__
                _model_loader.last_error_at = datetime.now(timezone.utc).isoformat()
                logger.error(f"Failed to load FasterQwen3TTS model: {e}")
                raise
    
    def get_runtime_status(self) -> Dict[str, Any]:
        return {
            "model_loaded": bool(_model_loader.model is not None and _model_loader.initialized),
            "runtime_code_dir": str(self._runtime_code_dir),
            "configured_model_source": str(self._model_config.get("model_name", "")),
            "last_error": _model_loader.last_error,
            "last_error_type": _model_loader.last_error_type,
            "last_error_at": _model_loader.last_error_at,
        }

    def _convert_audio_to_float32(self, audio_data: Union[bytes, np.ndarray], sample_rate: int = 24000) -> np.ndarray:
        """
        Convert audio data to float32 numpy array.
        
        Args:
            audio_data: Audio as bytes (WAV) or numpy array
            sample_rate: Expected sample rate
            
        Returns:
            Float32 mono audio array normalized to [-1, 1]
        """
        from io import BytesIO

        import numpy as np
        import soundfile as io
        
        if isinstance(audio_data, bytes):
            # Decode WAV bytes
            with BytesIO(audio_data) as bio:
                audio, sr = io.read(bio, dtype='float32', always_2d=False)
            if sr != sample_rate:
                # Simple resampling would be needed - for now require correct sample rate
                logger.warning(f"Sample rate mismatch: got {sr}, expected {sample_rate}")
        else:
            audio = audio_data
        
        # Ensure mono
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        
        # Normalize to [-1, 1] if needed
        max_val = np.max(np.abs(audio))
        if max_val > 1.0:
            audio = audio / max_val
            
        return audio.astype(np.float32)
    
    def _numpy_to_wav_bytes(self, audio: np.ndarray, sample_rate: int = 12000) -> bytes:
        """
        Convert float32 numpy array to WAV bytes.
        
        Args:
            audio: Float32 audio array
            sample_rate: Sample rate
            
        Returns:
            WAV file as bytes
        """
        from io import BytesIO

        import soundfile as io
        
        with BytesIO() as bio:
            io.write(bio, audio, sample_rate, format='WAV', subtype='PCM_16')
            return bio.getvalue()

    def _build_audio_response(
        self,
        *,
        wav_bytes: bytes,
        sample_rate: int,
        duration: float,
        raw_response: Any = None,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        audio_b64 = base64.b64encode(wav_bytes).decode('utf-8')
        payload = {
            "success": True,
            "audio": audio_b64,
            "audio_base64": audio_b64,
            "sample_rate": sample_rate,
            "duration": duration,
            "format": "audio/wav",
            "raw_response": raw_response,
        }
        if extra_fields:
            payload.update(extra_fields)
        return payload

    def _build_reference_fallback_response(
        self,
        *,
        text: str,
        error: Exception,
    ) -> Dict[str, Any]:
        sample_rate = self._sample_rate or 12000
        preview_duration = max(
            0.35,
            min(2.0, max(len(text.strip()), 1) / FALLBACK_CHARACTERS_PER_SECOND),
        )

        total_samples = max(int(sample_rate * preview_duration), sample_rate // 3)
        timeline = np.linspace(0.0, preview_duration, total_samples, endpoint=False, dtype=np.float32)
        carrier = 0.18 * np.sin(2.0 * np.pi * FALLBACK_CARRIER_FREQ_HZ * timeline)
        harmonic = 0.08 * np.sin(2.0 * np.pi * FALLBACK_HARMONIC_FREQ_HZ * timeline)
        audio = carrier + harmonic

        max_samples = max(int(sample_rate * preview_duration), 1)
        audio = np.asarray(audio[:max_samples], dtype=np.float32)
        audio = apply_fade(audio, fade_samples=min(256, max(16, len(audio) // 8)))
        wav_bytes = self._numpy_to_wav_bytes(audio, sample_rate)
        duration = len(audio) / sample_rate if sample_rate else 0.0
        audio_b64 = base64.b64encode(wav_bytes).decode("utf-8")
        return {
            "success": False,
            "audio": audio_b64,
            "audio_base64": audio_b64,
            "sample_rate": sample_rate,
            "duration": duration,
            "format": "audio/wav",
            "error": str(error),
            "raw_response": None,
            "provider_state": self.get_runtime_status(),
            "is_fallback": True,
            "fallback": "reference-preview",
            "fallback_reason": "temporary_synthesis_unavailable",
        }
    
    def get_speakers(self) -> List[Dict[str, Any]]:
        """
        Get list of available speakers/voices.
        
        For faster-qwen3-tts, speakers are derived from voice clones.

        Returns:
            List of speaker dictionaries with 'id', 'name', 'language' keys
        """
        return clone_speakers()

    def _map_language(self, language: Optional[str]) -> str:
        """Map language codes to model-supported language names."""
        if not language:
            return "English"
        
        # Language code to name mapping
        language_map = {
            'en': 'English',
            'zh': 'Chinese',
            'ja': 'Japanese',
            'fr': 'French',
            'de': 'German',
            'es': 'Spanish',
            'it': 'Italian',
            'ru': 'Russian',
            'ko': 'Korean',
            'pt': 'Portuguese',
        }
        
        # Return mapped language or original if not found
        return language_map.get(language.lower(), language)
    
    def generate_audio(
        self, text: str, speaker: Optional[str] = None,
        language: Optional[str] = None, **kwargs,
    ) -> Dict[str, Any]:
        progress_callback = kwargs.pop("_progress_callback", None)
        with generation_slot():
            return self._generate_audio_impl(
                text, speaker=speaker, language=language,
                progress_callback=progress_callback, **kwargs,
            )

    def generate_audio_with_progress(
        self,
        text: str,
        speaker: Optional[str] = None,
        language: Optional[str] = None,
        *,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate one clip while reporting codec-step progress."""
        if progress_callback is not None:
            try:
                progress_callback(0, int(kwargs.get("max_new_tokens") or self.max_seq_len))
            except Exception:
                logger.debug("unable to report initial TTS progress", exc_info=True)
        return self.generate_audio(
            text,
            speaker=speaker,
            language=language,
            _progress_callback=progress_callback,
            **kwargs,
        )

    def _generate_audio_impl(
        self,
        text: str,
        speaker: Optional[str] = None,
        language: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate audio from text using voice cloning.
        
        Args:
            text: Text to synthesize
            speaker: Speaker ID (refers to voice clone audio file)
            language: Language code (e.g., 'en', 'zh', 'ja')
            **kwargs: Additional parameters
                - ref_text: Transcription of reference audio
                - temperature: Sampling temperature (0.5-1.0)
                - top_k: Top-k sampling parameter
                - repetition_penalty: Repetition penalty factor
                - xvec_only: Use only speaker embedding (True) or full ICL (False)
                
        Returns:
            Dict with 'success', 'audio' (base64 WAV), 'sample_rate', 'duration'
        """
        try:
            ref_audio_path, saved_ref_text = _clone_reference(speaker)
            ref_text, xvec_only = _clone_conditioning(kwargs, self._model_config, saved_ref_text)
            
            if not ref_audio_path:
                return {
                    "success": False,
                    "error": "No reference audio available for voice cloning. Please upload a voice sample first."
                }
            
            # Load the model
            model = self._get_model()
            
            # Prepare generation parameters
            gen_kwargs = {
                'text': text,
                'language': self._map_language(language),
                'ref_audio': ref_audio_path,
                'ref_text': ref_text,
                'max_new_tokens': kwargs.get('max_new_tokens', self.max_seq_len),
                'min_new_tokens': kwargs.get('min_new_tokens', 2),
                'temperature': kwargs.get('temperature', self._model_config.get('temperature', 0.9)),
                'top_k': kwargs.get('top_k', self._model_config.get('top_k', 50)),
                'top_p': kwargs.get('top_p', self._model_config.get('top_p', 1.0)),
                'do_sample': kwargs.get('do_sample', self._model_config.get('do_sample', True)),
                'repetition_penalty': kwargs.get('repetition_penalty', self._model_config.get('repetition_penalty', 1.05)),
                'xvec_only': xvec_only,
                'non_streaming_mode': kwargs.get('non_streaming_mode', self._model_config.get('non_streaming_mode', True)),
                'append_silence': kwargs.get('append_silence', self._model_config.get('append_silence', True)),
                'parity_mode': _decode_in_parity_mode(model, kwargs, self._model_config),
                'progress_callback': progress_callback,
            }
            
            # Generate audio (non-streaming)
            try:
                audio_list, sample_rate = model.generate_voice_clone(**gen_kwargs)
            except Exception as exc:
                if not _is_retryable_generation_error(exc):
                    raise

                retry_attempts: list[tuple[str, dict[str, Any]]] = []
                if gen_kwargs.get('parity_mode') is True:
                    retry_attempts.append(("fresh parity model", dict(gen_kwargs)))
                else:
                    retry_kwargs = dict(gen_kwargs)
                    retry_kwargs['parity_mode'] = True
                    retry_attempts.append(("parity_mode=True", retry_kwargs))

                if gen_kwargs.get('non_streaming_mode') is True:
                    retry_kwargs = dict(gen_kwargs)
                    retry_kwargs['parity_mode'] = True
                    retry_kwargs['non_streaming_mode'] = False
                    retry_attempts.append(("parity_mode=True, non_streaming_mode=False", retry_kwargs))

                last_error: BaseException = exc
                for retry_label, retry_kwargs in retry_attempts:
                    logger.warning(
                        "[TTS] retryable generation failure; resetting model cache and retrying with %s: %s",
                        retry_label,
                        last_error,
                    )
                    _reset_cached_model_after_generation_error(last_error)
                    model = self._get_model()
                    try:
                        audio_list, sample_rate = model.generate_voice_clone(**retry_kwargs)
                        gen_kwargs = retry_kwargs
                        break
                    except Exception as retry_exc:
                        if not _is_retryable_generation_error(retry_exc):
                            raise
                        last_error = retry_exc
                else:
                    logger.warning(
                        "[TTS] batch generation retries failed; resetting model cache and stitching streaming fallback: %s",
                        last_error,
                    )
                    _reset_cached_model_after_generation_error(last_error)
                    model = self._get_model()
                    audio_list, sample_rate = _generate_audio_with_streaming_fallback(model, gen_kwargs)
            
            if not audio_list or len(audio_list) == 0:
                return {
                    "success": False,
                    "error": "No audio generated"
                }
            
            # Validate the raw waveform before encoding
            audio_np = audio_list[0]  # First (and only) audio array
            if not _is_valid_audio(audio_np):
                logger.warning("[TTS] Invalid audio detected – retrying once")
                audio_list, sample_rate = model.generate_voice_clone(**gen_kwargs)
                if not audio_list or not _is_valid_audio(audio_list[0]):
                    return {
                        "success": False,
                        "error": "Generated audio failed validation (corrupt or silent)"
                    }
                audio_np = audio_list[0]

            # Convert to WAV bytes
            wav_bytes = self._numpy_to_wav_bytes(audio_np, sample_rate)
            
            # Calculate duration
            duration = len(audio_np) / sample_rate
            logger.info("[TTS] generated chunk size=%d bytes, duration=%.2fs", len(wav_bytes), duration)
            
            response = self._build_audio_response(
                wav_bytes=wav_bytes,
                sample_rate=sample_rate,
                duration=duration,
                raw_response=None,
            )
            response["generation_parameters_used"] = {
                key: value for key, value in gen_kwargs.items()
                if key not in {"text", "ref_audio", "progress_callback"}
            }
            response["generation_strategy_revision"] = self.generation_strategy_revision
            return response

        except Exception as e:
                logger.error(f"Error in generate_audio: {e}", exc_info=True)
                try:
                    logger.warning("Returning explicit fallback failure response: %s", e)
                    return self._build_reference_fallback_response(
                        text=text,
                        error=e,
                    )
                except Exception as fallback_exc:
                    return {
                        "success": False,
                        "error": str(fallback_exc or e)
                    }
    
    def generate_audio_stream(
        self, text: str, speaker: Optional[str] = None,
        language: Optional[str] = None, **kwargs,
    ) -> Iterator[tuple[np.ndarray, int, dict]]:
        permit_already_held = bool(kwargs.pop("_device_permit_held", False))
        if permit_already_held:
            yield from self._generate_audio_stream_impl(
                text, speaker=speaker, language=language, **kwargs,
            )
            return
        with generation_slot():
            yield from self._generate_audio_stream_impl(
                text, speaker=speaker, language=language, **kwargs,
            )

    def _generate_audio_stream_impl(
        self,
        text: str,
        speaker: Optional[str] = None,
        language: Optional[str] = None,
        **kwargs
    ) -> Iterator[tuple[np.ndarray, int, dict]]:
        """
        Stream audio generation for real-time playback.
        
        Args:
            text: Text to synthesize
            speaker: Speaker ID (refers to voice clone audio file)
            language: Language code
            **kwargs: Same as generate_audio() plus:
                - chunk_size: Number of codec steps per chunk (default 12)
                
        Yields:
            Tuples of (audio_chunk_numpy, sample_rate, timing_dict)
        """
        provider_entry_at = time.perf_counter()
        try:
            ref_audio_path, saved_ref_text = _clone_reference(speaker)
            ref_text, xvec_only = _clone_conditioning(kwargs, self._model_config, saved_ref_text)
            
            if not ref_audio_path:
                raise Exception("No reference audio available for voice cloning")
            
            # Load the model
            model = self._get_model()
            
            # Prepare generation parameters
            gen_kwargs = {
                'text': text,
                'language': self._map_language(language),
                'ref_audio': ref_audio_path,
                'ref_text': ref_text,
                'max_new_tokens': kwargs.get('max_new_tokens', self.max_seq_len),
                'min_new_tokens': kwargs.get('min_new_tokens', 2),
                'temperature': kwargs.get('temperature', self._model_config.get('temperature', 0.9)),
                'top_k': kwargs.get('top_k', self._model_config.get('top_k', 50)),
                'top_p': kwargs.get('top_p', self._model_config.get('top_p', 1.0)),
                'do_sample': kwargs.get('do_sample', self._model_config.get('do_sample', True)),
                'repetition_penalty': kwargs.get('repetition_penalty', self._model_config.get('repetition_penalty', 1.05)),
                'chunk_size': kwargs.get('chunk_size', self._model_config.get('chunk_size', 12)),
                'xvec_only': xvec_only,
                'non_streaming_mode': kwargs.get('non_streaming_mode', self._model_config.get('non_streaming_mode', True)),
                'append_silence': kwargs.get('append_silence', self._model_config.get('append_silence', True)),
                'parity_mode': _decode_in_parity_mode(model, kwargs, self._model_config),
            }
            provider_setup_ms = (time.perf_counter() - provider_entry_at) * 1000.0
            
            # Stream generation – validate each chunk before yielding
            chunk_idx = 0
            try:
                for audio_chunk, sr, timing in model.generate_voice_clone_streaming(**gen_kwargs):
                    timing = dict(timing)
                    if chunk_idx == 0:
                        timing.update({
                            'provider_setup_ms': provider_setup_ms,
                            'provider_entry_to_first_audio_ms': (
                                time.perf_counter() - provider_entry_at
                            ) * 1000.0,
                        })
                    if not _is_valid_audio(audio_chunk):
                        logger.warning("[TTS] Skipping corrupt streaming chunk %d", chunk_idx)
                        chunk_idx += 1
                        continue
                    logger.info("[TTS] streaming chunk=%d size=%d samples", chunk_idx, len(audio_chunk))
                    yield audio_chunk, sr, timing
                    chunk_idx += 1
            except Exception as stream_exc:
                if not _is_retryable_generation_error(stream_exc):
                    raise
                if chunk_idx > 0:
                    logger.warning(
                        "[TTS] CUDA graph streaming failed after %d chunks; not retrying partial stream: %s",
                        chunk_idx,
                        stream_exc,
                    )
                    _reset_cached_model_after_generation_error(stream_exc)
                    raise

                retry_kwargs = dict(gen_kwargs)
                retry_kwargs["parity_mode"] = True
                logger.warning(
                    "[TTS] CUDA graph streaming failed before first chunk; resetting cache and retrying with parity_mode=True: %s",
                    stream_exc,
                )
                _reset_cached_model_after_generation_error(stream_exc)
                model = self._get_model()
                _disable_cuda_graphs_for_retry(model)
                try:
                    for audio_chunk, sr, timing in model.generate_voice_clone_streaming(**retry_kwargs):
                        if not _is_valid_audio(audio_chunk):
                            logger.warning("[TTS] Skipping corrupt streaming retry chunk %d", chunk_idx)
                            chunk_idx += 1
                            continue
                        logger.info("[TTS] streaming retry chunk=%d size=%d samples", chunk_idx, len(audio_chunk))
                        yield audio_chunk, sr, timing
                        chunk_idx += 1
                except Exception as retry_exc:
                    if not _is_retryable_generation_error(retry_exc):
                        raise
                    logger.warning(
                        "[TTS] CUDA graph streaming parity retry failed; resetting model cache and retrying once: %s",
                        retry_exc,
                    )
                    _reset_cached_model_after_generation_error(retry_exc)
                    model = self._get_model()
                    _disable_cuda_graphs_for_retry(model)
                    for audio_chunk, sr, timing in model.generate_voice_clone_streaming(**retry_kwargs):
                        if not _is_valid_audio(audio_chunk):
                            logger.warning("[TTS] Skipping corrupt streaming fresh retry chunk %d", chunk_idx)
                            chunk_idx += 1
                            continue
                        logger.info("[TTS] streaming fresh retry chunk=%d size=%d samples", chunk_idx, len(audio_chunk))
                        yield audio_chunk, sr, timing
                        chunk_idx += 1
                
        except Exception as e:
            logger.error(f"Error in generate_audio_stream: {e}", exc_info=True)
            if _is_retryable_generation_error(e):
                _reset_cached_model_after_generation_error(e)
            raise
    
    def voice_clone(
        self,
        voice_id: str,
        audio_data: bytes,
        ref_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a voice clone from audio data.
        
        For faster-qwen3-tts, voice cloning is done by saving the reference audio
        to the voice_clones directory. The actual cloning happens during generation.
        
        Args:
            voice_id: Unique identifier for the voice clone
            audio_data: Reference audio data (WAV format)
            ref_text: Optional transcription of the reference audio
            
        Returns:
            Dict with 'success', 'message', 'voice_id'
        """
        try:
            # Validate voice_id (alphanumeric + underscores)
            import re
            if not re.match(r'^[a-zA-Z0-9_\-]+$', voice_id):
                return {
                    "success": False,
                    "error": "Invalid voice ID. Use only letters, numbers, hyphens, and underscores."
                }
            
            # Ensure voice_clones directory exists
            voice_clones_dir = Path(VOICE_CLONES_DIR)
            voice_clones_dir.mkdir(parents=True, exist_ok=True)
            
            # Save the audio file
            output_path = voice_clones_dir / f"{voice_id}.wav"
            
            # Validate WAV format
            import wave
            with wave.open(BytesIO(audio_data), 'rb') as wav_file:
                channels = wav_file.getnchannels()
                framerate = wav_file.getframerate()
                frames = wav_file.getnframes()
                
                # Check if it's a valid WAV
                if channels not in [1, 2]:
                    return {
                        "success": False,
                        "error": f"Invalid WAV: must be mono or stereo, got {channels} channels"
                    }
            
            # Write the file
            output_path.write_bytes(audio_data)
            
            logger.info(f"Voice clone created: {voice_id} ({framerate} Hz, {frames} frames)")
            
            return {
                "success": True,
                "message": f"Voice clone '{voice_id}' created successfully",
                "voice_id": voice_id
            }
            
        except Exception as e:
            logger.error(f"Error in voice_clone: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def health_check(self) -> bool:
        """
        Check if the service is healthy.
        
        Returns:
            True if model is loaded and responsive, False otherwise
        """
        try:
            # Try to get the model (does not initialize if not ready)
            if _model_loader.model is not None and _model_loader.initialized:
                return True

            if _model_loader.last_error:
                logger.warning("Provider unhealthy due to previous model load failure: %s", _model_loader.last_error)
                return False
            
            # Quick test: can we import the library?
            tts_lib_path = self._runtime_code_dir
            if not tts_lib_path.exists():
                logger.warning(
                    "Vendored TTS runtime code path not found: %s",
                    tts_lib_path,
                )
                return False
            
            # Check if CUDA is available if using CUDA
            if self.device == 'cuda':
                import torch
                if not torch.cuda.is_available():
                    logger.warning("CUDA requested but not available")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    def test_connection(self) -> bool:
        """
        Test connection (alias for health_check).
        
        Returns:
            True if provider is ready, False otherwise
        """
        return self.health_check()
    
    def get_capabilities(self) -> List[AudioProviderCapability]:
        """
        Get list of capabilities supported by this provider.
        
        Returns:
            List of AudioProviderCapability enums
        """
        return self.default_capabilities.copy()
    
    def supports_streaming(self) -> bool:
        """Check if provider supports streaming TTS."""
        return True
    
    def supports_voice_cloning(self) -> bool:
        """Check if provider supports voice cloning."""
        return True
    
    def start(self) -> Dict[str, Any]:
        """
        Start the provider (initialize model).
        
        Returns:
            Dict with 'running' status and 'message'
        """
        try:
            self._validate_config()
            self._get_model()
            return {
                "running": True,
                "message": f"FasterQwen3TTS initialized with {self._model_config['model_name']}"
            }
        except Exception as e:
            logger.error(f"Failed to start provider: {e}")
            return {
                "running": False,
                "message": f"Failed to start: {str(e)}",
                "error": str(e),
                "provider_state": self.get_runtime_status(),
            }
    
    def stop(self) -> bool:
        """
        Stop the provider (unload model).
        
        Returns:
            True if stopped successfully
        """
        global _model_loader
        
        try:
            with _model_loader.lock:
                if _model_loader.model is not None:
                    # Clear the model to free memory
                    _model_loader.model = None
                    _model_loader.initialized = False
                    _model_loader.artifact_signature = None
                    # The vendored loader caches the same model; without this
                    # the weights stay on the GPU after an unload.
                    reset_tts_model_cache()
                    
                    # Force garbage collection
                    import gc

                    import torch
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    
                    logger.info("FasterQwen3TTS model unloaded")
                    
                return True
        except Exception as e:
            logger.error(f"Error stopping provider: {e}")
            return False
