"""
Vendored Qwen3-TTS adapter stable interface.

Only export public stable functions here.
Do NOT re-export raw vendored package internals.
"""

from .bootstrap import ensure_vendored_qwen3_tts_available
from .loader import get_or_create_tts_model, reset_tts_model_cache
from .speakers import list_available_speakers
from .synth import synthesize_speech
from .types import ModelConfig, RuntimeStatus, SpeakerRecord, SynthesisResult

__all__ = [
    "ensure_vendored_qwen3_tts_available",
    "get_or_create_tts_model",
    "reset_tts_model_cache",
    "list_available_speakers",
    "synthesize_speech",
    "SpeakerRecord",
    "SynthesisResult",
    "RuntimeStatus",
    "ModelConfig",
]
