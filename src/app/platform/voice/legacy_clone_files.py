"""Retire local voice-clone files after their shared asset is deleted."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.assets.content import local_asset_path
from app.assets import AssetRecord
from app.assets.canonical_voice_clones import canonical_voice_clone_root
from app.runtime.paths import VOICE_CLONES_DIR, VOICE_CLONES_FILE


def delete_legacy_voice_clone_files(asset: AssetRecord) -> dict[str, Any]:
    """Remove the local clone source and manifest entry so it cannot reappear."""

    clone_dir = Path(str(VOICE_CLONES_DIR)).resolve()
    manifest_path = Path(str(VOICE_CLONES_FILE)).resolve()
    metadata = dict(asset.metadata or {})
    identifiers = {
        str(value).strip().casefold()
        for value in (
            asset.id.removeprefix("voice-cloning:"),
            metadata.get("profile_name"),
            metadata.get("voice_id"),
            metadata.get("voice_clone_id"),
        )
        if str(value or "").strip()
    }
    removed_ids: set[str] = set()
    manifest_changed = False
    try:
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file()
            else {}
        )
    except Exception:
        manifest = {}
    if isinstance(manifest, dict):
        for name, value in list(manifest.items()):
            row = value if isinstance(value, dict) else {}
            clone_id = str(row.get("voice_clone_id") or name).strip()
            if str(name).casefold() in identifiers or clone_id.casefold() in identifiers:
                manifest.pop(name, None)
                removed_ids.add(clone_id)
                manifest_changed = True
        if manifest_changed:
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    removed_ids.update(str(value) for value in identifiers if value)
    file_deleted = False
    for clone_id in removed_ids:
        for suffix in (
            ".wav",
            ".mp3",
            ".mp4",
            ".m4a",
            ".webm",
            ".ogg",
            ".flac",
            ".json",
        ):
            target = (clone_dir / f"{clone_id}{suffix}").resolve()
            if target.parent != clone_dir or target == manifest_path or not target.is_file():
                continue
            target.unlink()
            file_deleted = True
    # Legacy clones are local files (possibly already removed above); remote
    # blob-backed profiles have no local path.
    local = local_asset_path(asset)
    source = local.resolve() if local is not None else Path()
    allowed_roots = (clone_dir, canonical_voice_clone_root().resolve())
    if (
        source.suffix.lower()
        in {".wav", ".mp3", ".mp4", ".m4a", ".webm", ".ogg", ".flac"}
        and any(source.is_relative_to(root) for root in allowed_roots)
        and source.parent.is_dir()
    ):
        for target in source.parent.iterdir():
            if (
                target.is_file()
                and target.stem.casefold() == source.stem.casefold()
                and target.suffix.lower()
                in {
                    ".wav",
                    ".mp3",
                    ".mpeg",
                    ".mp4",
                    ".m4a",
                    ".webm",
                    ".ogg",
                    ".flac",
                    ".json",
                }
            ):
                target.unlink()
                file_deleted = True
    return {"manifest_deleted": manifest_changed, "file_deleted": file_deleted}
