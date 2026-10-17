"""
Speech generation wrapper for vendored Qwen3-TTS.

Handles input validation, parameter normalization, and output conversion
to stable internal contracts.
"""

import logging
from pathlib import Path
from typing import Optional

from app.runtime.paths import VOICE_CLONES_DIR

from .types import SynthesisResult

logger = logging.getLogger(__name__)


def _resolve_reference_audio(speaker: Optional[str] = None) -> Optional[str]:
    """Resolve reference audio path for given speaker ID."""
    if not speaker:
        speaker = "default"
    
    voice_clones_dir = Path(VOICE_CLONES_DIR)
    
    # Check exact speaker file
    ref_path = voice_clones_dir / f"{speaker}.wav"
    if ref_path.exists():
        return str(ref_path)
    
    # Check default reference
    default_ref = voice_clones_dir / "default_ref.wav"
    if default_ref.exists():
        return str(default_ref)
    
    # Fallback to first available wav file
    if voice_clones_dir.exists():
        wav_files = list(voice_clones_dir.glob('*.wav'))
        if wav_files:
            return str(wav_files[0])
    
    return None


def _map_language(language: Optional[str]) -> str:
    """Map language codes to model-supported language names."""
    if not language:
        return "English"
    
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
    
    return language_map.get(language.lower(), language)


def synthesize_speech(
    model,
    *,
    text: str,
    speaker: Optional[str] = None,
    language: Optional[str] = None,
    speed: Optional[float] = None,
    temperature: Optional[float] = None,
    seed: Optional[int] = None,
    **kwargs
) -> SynthesisResult:
    """
    Generate speech and return normalized audio result.
    
    Args:
        model: Loaded Qwen3-TTS model instance
        text: Text to synthesize
        speaker: Speaker ID
        language: Language code
        speed: Speech speed multiplier (reserved for future use)
        temperature: Sampling temperature
        seed: Random seed (reserved for future use)
        **kwargs: Additional generation parameters
    
    Returns:
        Normalized SynthesisResult object
    
    Raises:
        ValueError: If inputs are invalid
        RuntimeError: If synthesis fails
    """
    if not text or not text.strip():
        raise ValueError("Text cannot be empty")
    
    ref_audio_path = _resolve_reference_audio(speaker)
    
    if not ref_audio_path:
        raise RuntimeError(
            "No reference audio available for voice cloning. "
            "Please upload a voice sample first."
        )
    
    # Prepare generation parameters
    gen_kwargs = {
        'text': text,
        'language': _map_language(language),
        'ref_audio': ref_audio_path,
        'ref_text': kwargs.get('ref_text', ''),
        'max_new_tokens': kwargs.get('max_new_tokens', 2048),
        'min_new_tokens': kwargs.get('min_new_tokens', 2),
        'temperature': temperature if temperature is not None else 0.9,
        'top_k': kwargs.get('top_k', 50),
        'top_p': kwargs.get('top_p', 1.0),
        'do_sample': kwargs.get('do_sample', True),
        'repetition_penalty': kwargs.get('repetition_penalty', 1.05),
        'xvec_only': kwargs.get('xvec_only', True),
        'non_streaming_mode': kwargs.get('non_streaming_mode', True),
        'append_silence': kwargs.get('append_silence', True),
    }
    
    try:
        audio_list, sample_rate = model.generate_voice_clone(**gen_kwargs)
        
        if not audio_list or len(audio_list) == 0:
            raise RuntimeError("Model returned empty audio result")
        
        audio_np = audio_list[0]
        
        return SynthesisResult(
            sample_rate=sample_rate,
            audio=audio_np,
            format="wav",
            speaker=speaker,
            metadata={
                'reference_audio': ref_audio_path,
                'language': gen_kwargs['language'],
                'temperature': gen_kwargs['temperature']
            }
        )
        
    except Exception as e:
        logger.error(f"Speech synthesis failed: {e}", exc_info=True)
        raise RuntimeError(f"Synthesis failed: {str(e)}") from e