"""
Vendored Qwen3-TTS bootstrap loader.

Validates vendored packages exist under repo source tree.
No sys.path modification, no fallback to pip packages.
"""

from pathlib import Path
from typing import Dict


def ensure_vendored_qwen3_tts_available() -> Dict[str, str]:
    """
    Validate that vendored faster_qwen3_tts and qwen_tts packages exist
    under src/app/providers/vendor and return their resolved paths.
    Raise RuntimeError with a clear message if missing.
    """
    # Locate vendor root relative to this file
    current_file = Path(__file__).resolve()
    vendor_root = current_file.parent.parent

    faster_qwen3_tts_path = vendor_root / "faster_qwen3_tts"
    qwen_tts_path = vendor_root / "qwen_tts"

    # Validate package directories exist
    if not faster_qwen3_tts_path.exists():
        raise RuntimeError(
            f"Vendored faster_qwen3_tts package not found at: {faster_qwen3_tts_path}\n"
            "Ensure repository source tree is complete."
        )

    if not qwen_tts_path.exists():
        raise RuntimeError(
            f"Vendored qwen_tts package not found at: {qwen_tts_path}\n"
            "Ensure repository source tree is complete."
        )

    # Validate __init__.py exists for both packages
    faster_init = faster_qwen3_tts_path / "__init__.py"
    qwen_init = qwen_tts_path / "__init__.py"

    if not faster_init.exists():
        raise RuntimeError(
            f"Vendored faster_qwen3_tts package is missing __init__.py at: {faster_init}\n"
            "Package structure is corrupted."
        )

    if not qwen_init.exists():
        raise RuntimeError(
            f"Vendored qwen_tts package is missing __init__.py at: {qwen_init}\n"
            "Package structure is corrupted."
        )

    return {
        "vendor_root": str(vendor_root),
        "faster_qwen3_tts": str(faster_qwen3_tts_path),
        "qwen_tts": str(qwen_tts_path),
    }