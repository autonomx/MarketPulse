"""
Local type definitions for vendored Qwen3-TTS adapter.

These types isolate the rest of the application from upstream
vendored package shape changes.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union

import numpy as np


@dataclass
class SpeakerRecord:
    """Normalized speaker metadata record."""
    id: str
    name: str
    label: Optional[str] = None
    language: Optional[str] = None
    gender: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SynthesisResult:
    """Normalized speech synthesis result."""
    sample_rate: int
    audio: Union[np.ndarray, bytes]
    format: str = "wav"
    speaker: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeStatus:
    """Runtime validation status."""
    ok: bool
    provider: str = "faster-qwen3-tts"
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelConfig:
    """Model configuration parameters."""
    model_name: str
    device: str = "cuda"
    dtype: str = "bfloat16"
    max_seq_len: int = 2048
    temperature: float = 0.9
    top_k: int = 50
    top_p: float = 1.0
    repetition_penalty: float = 1.05
    chunk_size: int = 12
    xvec_only: bool = True