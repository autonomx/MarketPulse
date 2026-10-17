"""
Normalized speaker enumeration for vendored Qwen3-TTS.
"""

from pathlib import Path
from typing import Any, Dict, List

from app.runtime.paths import VOICE_CLONES_DIR


def list_available_speakers(model: Any = None) -> List[Dict[str, Any]]:
    """
    Return normalized speaker records for API/UI use.
    
    Args:
        model: Optional model instance (not used currently but reserved for future use)
    
    Returns:
        List of standardized speaker dictionaries with stable shape
    """
    speakers: List[Dict[str, Any]] = []
    
    # Add default speaker
    speakers.append({
        "id": "default",
        "name": "Default",
        "label": "Default Qwen3-TTS Voice",
        "language": "en",
        "gender": None,
        "metadata": {
            "builtin": True
        }
    })
    
    # Add custom voice clones
    voice_clones_dir = Path(VOICE_CLONES_DIR)
    
    if voice_clones_dir.exists():
        for wav_file in voice_clones_dir.glob('*.wav'):
            voice_id = wav_file.stem
            
            speakers.append({
                "id": voice_id,
                "name": voice_id,
                "label": f"Custom Voice: {voice_id}",
                "language": "en",
                "gender": None,
                "metadata": {
                    "builtin": False,
                    "path": str(wav_file),
                    "size_bytes": wav_file.stat().st_size
                }
            })
    
    return speakers