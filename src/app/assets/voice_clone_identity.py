"""Shared conditioning identity for local voice clone references."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def reference_transcript(audio_path: str | Path) -> str:
    path = Path(audio_path)
    sidecar = next((item for item in path.parent.glob("*.json")
                    if item.stem.casefold() == path.stem.casefold()), None)
    if sidecar is None:
        return ""
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        return str(payload.get("ref_text") or "").strip() if isinstance(payload, dict) else ""
    except (OSError, ValueError, TypeError):
        return ""


def voice_reference_revision(audio_path: str | Path) -> str:
    audio_hash = hashlib.sha256(Path(audio_path).read_bytes()).hexdigest()
    transcript = reference_transcript(audio_path)
    if not transcript:
        return audio_hash
    identity = json.dumps({"version": "voice-reference-v1", "audio_hash": audio_hash,
                           "ref_text": transcript}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
