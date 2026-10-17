"""Unconditional read-through for the canonical resources/voice_clones folder."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.paths import resources_root

from .content import asset_location
from .models import AssetRecord, AssetType

LOGGER = logging.getLogger("uvicorn.error")

_AUDIO_MIME_TYPES = {
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}
_GENERIC_AUDIO_NAMES = {"audio", "clone", "reference", "sample", "source", "voice"}


def canonical_voice_clone_root() -> Path:
    """Return the repository-owned clone directory that must always be scanned."""

    return resources_root() / "voice_clones"


def discover_canonical_voice_clone_assets() -> list[AssetRecord]:
    """Scan the repository's canonical clone folder regardless of env overrides.

    ``OMNIX_VOICE_CLONES_DIR`` remains useful as an additional compatibility
    source, but it must never hide files physically stored in
    ``resources/voice_clones``.

    Stable asset IDs continue to come from the actual file path. Speaker metadata
    is hydrated from ``voice_clones.json`` using a case-insensitive match so a file
    such as ``jinx.wav`` can retain ``voice-cloning:jinx`` while TTS receives the
    registry's authoritative case-sensitive clone ID, for example ``Jinx``.
    """

    clone_root = canonical_voice_clone_root()
    files = _audio_files(clone_root)
    manifest_entries = _manifest_entries(clone_root / "voice_clones.json")
    records: dict[str, AssetRecord] = {}

    for audio_path in files:
        inferred_profile_name = _profile_name(clone_root, audio_path)
        stable_voice_id = _safe_segment(inferred_profile_name)
        manifest_name, manifest_data = _matching_manifest_entry(
            manifest_entries,
            inferred_profile_name,
            audio_path,
        )
        profile_name = _text(manifest_data.get("profile_name")) or manifest_name or inferred_profile_name
        voice_id = _text(manifest_data.get("voice_id")) or manifest_name or stable_voice_id
        voice_clone_id = _text(manifest_data.get("voice_clone_id")) or voice_id
        speaker = _text(manifest_data.get("speaker")) or profile_name
        try:
            relative_path = str(audio_path.relative_to(clone_root))
        except ValueError:
            relative_path = audio_path.name

        asset = AssetRecord(
            id=f"voice-cloning:{stable_voice_id}",
            module="voice-cloning",
            type=AssetType.VOICE_PROFILE,
            mime_type=_AUDIO_MIME_TYPES.get(audio_path.suffix.lower(), "application/octet-stream"),
            storage_path=str(audio_path),
            metadata={
                "profile_name": profile_name,
                "voice_id": voice_id,
                "voice_clone_id": voice_clone_id,
                "speaker": speaker,
                "language": manifest_data.get("language") or "",
                "gender": manifest_data.get("gender") or "neutral",
                "has_audio": True,
                "is_preloaded": bool(manifest_data.get("is_preloaded")),
                "recovered_from_canonical_file": True,
                "resolved_from_voice_manifest": bool(manifest_data),
                "relative_path": relative_path,
            },
            created_at=_mtime_iso(audio_path),
            compat={
                "legacy_system": "canonical resources/voice_clones directory scan",
                "legacy_voice_id": voice_id,
                "voice_clone_root": str(clone_root),
                "voice_manifest": str(clone_root / "voice_clones.json") if manifest_data else "",
            },
        )
        records.setdefault(asset.id, asset)

    LOGGER.info(
        "[Voice Library][canonical] scanned root=%s exists=%s audio_count=%d profiles=%s",
        clone_root,
        clone_root.is_dir(),
        len(files),
        [
            {
                "id": asset.id,
                "name": asset.metadata.get("profile_name"),
                "voice_id": asset.metadata.get("voice_id"),
                "voice_clone_id": asset.metadata.get("voice_clone_id"),
                "path": asset_location(asset),
            }
            for asset in records.values()
        ][:100],
    )
    return [records[asset_id] for asset_id in sorted(records)]


def _manifest_entries(manifest_path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}

    for wrapper_key in ("voices", "voice_clones", "profiles"):
        if isinstance(raw, dict) and isinstance(raw.get(wrapper_key), (dict, list)):
            raw = raw[wrapper_key]
            break

    entries: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                entries[str(key)] = dict(value)
    elif isinstance(raw, list):
        for index, value in enumerate(raw):
            if not isinstance(value, dict):
                continue
            name = (
                _text(value.get("profile_name"))
                or _text(value.get("voice_id"))
                or _text(value.get("voice_clone_id"))
                or f"voice-{index + 1}"
            )
            entries[name] = dict(value)
    return entries


def _matching_manifest_entry(
    entries: dict[str, dict[str, Any]],
    inferred_profile_name: str,
    audio_path: Path,
) -> tuple[str, dict[str, Any]]:
    candidates = {
        inferred_profile_name.casefold(),
        audio_path.stem.casefold(),
        _safe_segment(inferred_profile_name).casefold(),
    }
    for name, data in entries.items():
        metadata_candidates = {
            name.casefold(),
            _text(data.get("profile_name")).casefold(),
            _text(data.get("voice_id")).casefold(),
            _text(data.get("voice_clone_id")).casefold(),
            Path(_text(data.get("source_path")) or _text(data.get("audio_path"))).stem.casefold(),
        }
        metadata_candidates.discard("")
        if candidates & metadata_candidates:
            return name, data
    return "", {}


def _audio_files(clone_root: Path) -> list[Path]:
    try:
        if not clone_root.is_dir():
            return []
    except OSError:
        LOGGER.exception("[Voice Library][canonical] root could not be inspected path=%s", clone_root)
        return []

    files: list[Path] = []

    def handle_walk_error(error: OSError) -> None:
        LOGGER.warning(
            "[Voice Library][canonical] directory walk error root=%s error=%s",
            clone_root,
            error,
        )

    try:
        for root, _directories, names in os.walk(clone_root, onerror=handle_walk_error):
            for name in names:
                path = Path(root) / name
                if path.suffix.lower() in _AUDIO_MIME_TYPES:
                    files.append(path)
    except OSError:
        LOGGER.exception("[Voice Library][canonical] directory walk failed root=%s", clone_root)
    return sorted(files, key=lambda path: os.path.normcase(str(path)))


def _profile_name(clone_root: Path, audio_path: Path) -> str:
    stem = audio_path.stem.strip()
    if stem.casefold() in _GENERIC_AUDIO_NAMES and audio_path.parent != clone_root:
        return audio_path.parent.name.strip() or stem or "voice"
    return stem or audio_path.parent.name.strip() or "voice"


def _safe_segment(value: str) -> str:
    normalized = value.strip().replace("\\", "/").replace(":", "-")
    pieces = [piece for part in normalized.split("/") for piece in part.split()]
    return "-".join(pieces) or "unnamed"


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return datetime.now(timezone.utc).isoformat()
