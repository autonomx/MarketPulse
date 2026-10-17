"""Resolve a speaker to its saved voice-clone sample and transcript.

Every local TTS provider clones from the same Voice Library, so they share
this lookup: a library asset id (``voice-cloning:<id>``) or a file stem in
the voice-clone folder.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.assets.voice_clone_identity import reference_transcript
from app.runtime.paths import VOICE_CLONES_DIR


def clone_reference(speaker: Optional[str]) -> tuple[Optional[str], str]:
    """Resolve a saved clone and its exact reference transcript together."""
    if speaker:
        from app.assets.canonical_voice_clones import discover_canonical_voice_clone_assets
        from app.assets.content import asset_available, materialize_asset

        clone_id = speaker.removeprefix("voice-cloning:").casefold()
        for asset in discover_canonical_voice_clone_assets():
            if asset.id.removeprefix("voice-cloning:").casefold() == clone_id and asset_available(asset):
                path = materialize_asset(asset)
                return str(path), reference_transcript(path)
        if speaker.startswith("voice-cloning:"):
            return None, ""

    clone_dir = Path(VOICE_CLONES_DIR)
    if speaker:
        path = clone_dir / f"{speaker}.wav"
        if path.is_file():
            return str(path), ""
    default_ref = clone_dir / "default_ref.wav"
    if default_ref.is_file():
        return str(default_ref), ""
    return next((str(path) for path in clone_dir.glob("*.wav") if path.is_file()), None), ""


def clone_speakers() -> list[dict[str, Any]]:
    """The default speaker plus one entry per saved WAV clone."""
    speakers: list[dict[str, Any]] = [
        {"id": "default", "name": "Default", "language": "en", "description": "Default voice"},
    ]
    clone_dir = Path(VOICE_CLONES_DIR)
    if clone_dir.exists():
        for wav_file in clone_dir.glob("*.wav"):
            speakers.append({
                "id": wav_file.stem,
                "name": wav_file.stem,
                "language": "en",
                "path": str(wav_file),
                "description": f"Custom voice: {wav_file.stem}",
            })
    return speakers
