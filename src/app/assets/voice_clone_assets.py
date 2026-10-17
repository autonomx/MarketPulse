"""Direct discovery of locally stored voice-clone profiles."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.env import env_str
from app.runtime.paths import repo_root, resources_root

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


def voice_clone_sources() -> list[tuple[Path, Path]]:
    """Return authoritative voice-clone directories and their metadata manifests.

    ``resources/voice_clones`` is always the default source. Environment overrides
    are authoritative for tests and alternate deployments. No legacy file fallback is consulted.
    """

    override_dir = env_str("OMNIX_VOICE_CLONES_DIR", "") or ""
    override_file = env_str("OMNIX_VOICE_CLONES_FILE", "") or ""
    if override_dir:
        clones_dir = Path(override_dir)
        manifest_path = Path(override_file) if override_file else clones_dir / "voice_clones.json"
        override: list[tuple[Path, Path]] = [(clones_dir, manifest_path)]
        LOGGER.info(
            "[Voice Library][scan] using environment override sources=%s",
            _source_debug_rows(override),
        )
        return override

    canonical_dir = resources_root() / "voice_clones"
    canonical_manifest = Path(override_file) if override_file else canonical_dir / "voice_clones.json"
    sources: list[tuple[Path, Path]] = [(canonical_dir, canonical_manifest)]


    deduplicated: list[tuple[Path, Path]] = []
    seen: set[tuple[str, str]] = set()
    for clones_dir, manifest_path in sources:
        key = (_path_key(clones_dir), _path_key(manifest_path))
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append((clones_dir, manifest_path))

    LOGGER.info(
        "[Voice Library][scan] resolved sources repo_root=%s resources_root=%s sources=%s",
        repo_root(),
        resources_root(),
        _source_debug_rows(deduplicated),
    )
    return deduplicated


def discover_voice_clone_assets() -> list[AssetRecord]:
    """Build voice-profile assets directly from ``resources/voice_clones``.

    Discovery is deliberately best-effort. One malformed manifest row, unreadable
    sidecar, or inaccessible compatibility source must never make ``/api/assets``
    fail or hide otherwise valid local clone files.
    """

    records: dict[str, AssetRecord] = {}
    referenced_audio_paths: set[str] = set()
    sources = voice_clone_sources()
    LOGGER.info(
        "[Voice Library][scan] discovery started source_count=%d",
        len(sources),
    )

    for clones_dir, manifest_path in sources:
        try:
            audio_files = _audio_files(clones_dir)
            manifest_entries = _load_manifest_entries(manifest_path)
        except Exception:
            LOGGER.exception(
                "[Voice Library][scan] source initialization failed directory=%s manifest=%s",
                clones_dir,
                manifest_path,
            )
            continue

        LOGGER.info(
            "[Voice Library][scan] source inspected directory=%s directory_exists=%s "
            "manifest=%s manifest_exists=%s audio_count=%d manifest_entries=%d audio_files=%s",
            clones_dir,
            _safe_is_dir(clones_dir),
            manifest_path,
            _safe_is_file(manifest_path),
            len(audio_files),
            len(manifest_entries),
            [str(path) for path in audio_files[:100]],
        )

        for legacy_name, data in sorted(manifest_entries.items()):
            try:
                profile_name = _text(data.get("profile_name")) or legacy_name
                voice_id = _text(data.get("voice_id")) or legacy_name
                clone_id = _text(data.get("voice_clone_id")) or voice_id
                audio_path = _resolve_audio_path(clones_dir, clone_id, data, audio_files)
                if audio_path is not None:
                    referenced_audio_paths.add(_path_key(audio_path))

                storage_path = str(audio_path or manifest_path)
                mime_type = (
                    _AUDIO_MIME_TYPES.get(audio_path.suffix.lower(), "application/octet-stream")
                    if audio_path
                    else "application/json"
                )
                asset = AssetRecord(
                    id=_safe_voice_asset_id(profile_name),
                    module="voice-cloning",
                    type=AssetType.VOICE_PROFILE,
                    mime_type=mime_type,
                    storage_path=storage_path,
                    metadata={
                        "profile_name": profile_name,
                        "voice_id": voice_id,
                        "voice_clone_id": clone_id,
                        "speaker": data.get("speaker") or profile_name,
                        "language": data.get("language") or "",
                        "gender": data.get("gender") or "neutral",
                        "has_audio": audio_path is not None or bool(data.get("has_audio")),
                        "is_preloaded": bool(data.get("is_preloaded")),
                        "source_path": str(data.get("source_path") or data.get("audio_path") or ""),
                    },
                    created_at=_mtime_iso(audio_path or manifest_path),
                    compat={
                        "legacy_system": "resources/voice_clones manifest",
                        "legacy_manifest": str(manifest_path),
                        "legacy_voice_id": legacy_name,
                        "voice_clone_root": str(clones_dir),
                    },
                )
                records[asset.id] = asset
            except Exception:
                LOGGER.exception(
                    "[Voice Library][scan] manifest row failed directory=%s manifest=%s row=%s data=%r",
                    clones_dir,
                    manifest_path,
                    legacy_name,
                    data,
                )

        for audio_path in audio_files:
            if _path_key(audio_path) in referenced_audio_paths:
                continue
            try:
                sidecar_path, sidecar_data = _read_sidecar(audio_path)
                inferred_name = _inferred_profile_name(clones_dir, audio_path)
                profile_name = (
                    _text(sidecar_data.get("profile_name"))
                    or _text(sidecar_data.get("display_name"))
                    or _text(sidecar_data.get("voice_name"))
                    or inferred_name
                )
                voice_id = (
                    _text(sidecar_data.get("voice_id"))
                    or _text(sidecar_data.get("voice_clone_id"))
                    or _safe_segment(profile_name)
                )
                try:
                    relative_path = str(audio_path.relative_to(clones_dir))
                except ValueError:
                    relative_path = audio_path.name

                asset = AssetRecord(
                    id=_safe_voice_asset_id(profile_name),
                    module="voice-cloning",
                    type=AssetType.VOICE_PROFILE,
                    mime_type=_AUDIO_MIME_TYPES.get(
                        audio_path.suffix.lower(),
                        "application/octet-stream",
                    ),
                    storage_path=str(audio_path),
                    metadata={
                        "profile_name": profile_name,
                        "voice_id": voice_id,
                        "voice_clone_id": voice_id,
                        "speaker": sidecar_data.get("speaker") or profile_name,
                        "language": sidecar_data.get("language") or "",
                        "gender": sidecar_data.get("gender") or "neutral",
                        "has_audio": True,
                        "is_preloaded": bool(sidecar_data.get("is_preloaded")),
                        "recovered_from_file": True,
                        "relative_path": relative_path,
                    },
                    created_at=_mtime_iso(audio_path),
                    compat={
                        "legacy_system": "resources/voice_clones directory scan",
                        "legacy_voice_id": voice_id,
                        "voice_clone_root": str(clones_dir),
                        "metadata_sidecar": str(sidecar_path) if sidecar_path else "",
                    },
                )
                records.setdefault(asset.id, asset)
            except Exception:
                LOGGER.exception(
                    "[Voice Library][scan] audio file failed directory=%s audio=%s",
                    clones_dir,
                    audio_path,
                )

    discovered = [records[asset_id] for asset_id in sorted(records)]
    LOGGER.info(
        "[Voice Library][scan] discovery completed voice_profile_count=%d voices=%s",
        len(discovered),
        [
            {
                "id": asset.id,
                "name": asset.metadata.get("profile_name") or asset.metadata.get("voice_id"),
                "path": asset_location(asset),
            }
            for asset in discovered[:100]
        ],
    )
    return discovered


def _audio_files(clones_dir: Path) -> list[Path]:
    """Return supported clone audio without failing on one unreadable directory."""

    try:
        if not clones_dir.is_dir():
            LOGGER.warning(
                "[Voice Library][scan] clone directory is missing or not a directory path=%s",
                clones_dir,
            )
            return []
    except OSError:
        LOGGER.exception(
            "[Voice Library][scan] clone directory could not be inspected path=%s",
            clones_dir,
        )
        return []

    files: list[Path] = []

    def handle_walk_error(error: OSError) -> None:
        LOGGER.warning(
            "[Voice Library][scan] directory walk error path=%s error=%s",
            clones_dir,
            error,
        )

    try:
        for root, _directories, names in os.walk(clones_dir, onerror=handle_walk_error):
            for name in names:
                path = Path(root) / name
                if path.suffix.lower() in _AUDIO_MIME_TYPES:
                    files.append(path)
    except OSError:
        LOGGER.exception(
            "[Voice Library][scan] directory walk failed path=%s",
            clones_dir,
        )
    return sorted(files, key=_path_key)


def _load_manifest_entries(manifest_path: Path) -> dict[str, dict[str, Any]]:
    raw: Any = {}
    try:
        if manifest_path.is_file():
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        LOGGER.exception(
            "[Voice Library][scan] voice manifest could not be parsed path=%s",
            manifest_path,
        )
        raw = {}

    for wrapper_key in ("voices", "voice_clones", "profiles"):
        if isinstance(raw, dict) and isinstance(raw.get(wrapper_key), (dict, list)):
            raw = raw[wrapper_key]
            break

    entries: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for index, payload in enumerate(raw):
            if not isinstance(payload, dict):
                continue
            name = (
                _text(payload.get("profile_name"))
                or _text(payload.get("voice_id"))
                or _text(payload.get("voice_clone_id"))
                or f"voice-{index + 1}"
            )
            entries[name] = dict(payload)
    elif isinstance(raw, dict):
        for voice_id, payload in raw.items():
            if isinstance(payload, dict):
                entries[str(voice_id)] = dict(payload)
            elif isinstance(payload, str) and payload.strip():
                entries[str(voice_id)] = {
                    "profile_name": str(voice_id),
                    "source_path": payload,
                }
    return entries


def _resolve_audio_path(
    clones_dir: Path,
    clone_id: str,
    data: dict[str, Any],
    audio_files: list[Path],
) -> Path | None:
    candidates: list[Path] = []
    for key in ("source_path", "audio_path", "storage_path"):
        value = _text(data.get(key))
        if not value:
            continue
        path = Path(value)
        candidates.append(path)
        if not path.is_absolute():
            candidates.extend((repo_root() / path, clones_dir / path, clones_dir / path.name))

    clone_name = Path(clone_id).name
    if clone_name:
        candidates.append(clones_dir / clone_name)
        stem = Path(clone_name).stem
        candidates.extend(clones_dir / f"{stem}{suffix}" for suffix in _AUDIO_MIME_TYPES)
        candidates.extend(
            path
            for path in audio_files
            if path.stem.casefold() == stem.casefold()
        )

    seen: set[str] = set()
    for candidate in candidates:
        key = _path_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            if candidate.is_file() and candidate.suffix.lower() in _AUDIO_MIME_TYPES:
                return candidate
        except OSError:
            LOGGER.warning(
                "[Voice Library][scan] candidate audio path could not be inspected path=%s",
                candidate,
            )
    return None


def _read_sidecar(audio_path: Path) -> tuple[Path | None, dict[str, Any]]:
    candidates = [
        audio_path.with_suffix(".json"),
        audio_path.parent / "metadata.json",
        audio_path.parent / "profile.json",
        audio_path.parent / "voice.json",
    ]
    for sidecar_path in candidates:
        if sidecar_path.name == "voice_clones.json":
            continue
        try:
            if not sidecar_path.is_file():
                continue
            raw = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.exception(
                "[Voice Library][scan] sidecar could not be read audio=%s sidecar=%s",
                audio_path,
                sidecar_path,
            )
            continue
        if isinstance(raw, dict):
            return sidecar_path, raw
    return None, {}


def _inferred_profile_name(clones_dir: Path, audio_path: Path) -> str:
    stem = audio_path.stem.strip()
    if stem.casefold() in _GENERIC_AUDIO_NAMES and audio_path.parent != clones_dir:
        return audio_path.parent.name
    return stem or audio_path.parent.name or "voice"


def _safe_voice_asset_id(value: str) -> str:
    return f"voice-cloning:{_safe_segment(value)}"


def _safe_segment(value: str) -> str:
    normalized = value.strip().replace("\\", "/").replace(":", "-")
    pieces = [piece for part in normalized.split("/") for piece in part.split()]
    return "-".join(pieces) or "unnamed"


def _source_debug_rows(sources: list[tuple[Path, Path]]) -> list[dict[str, Any]]:
    return [
        {
            "directory": str(clones_dir),
            "directory_exists": _safe_is_dir(clones_dir),
            "manifest": str(manifest_path),
            "manifest_exists": _safe_is_file(manifest_path),
        }
        for clones_dir, manifest_path in sources
    ]


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except (OSError, RuntimeError):
        return os.path.normcase(str(path.absolute()))


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""
