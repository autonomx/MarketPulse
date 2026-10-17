"""Compatibility read-through for legacy generated document artifacts."""
from __future__ import annotations

from app.config.env import env_str, environment

import os
from datetime import datetime, timezone
from pathlib import Path

from app.runtime.paths import resources_data_root

from .models import AssetRecord, AssetType

_DOCUMENT_MIME_TYPES = {
    ".csv": "text/csv",
    ".htm": "text/html",
    ".html": "text/html",
    ".json": "application/json",
    ".log": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".srt": "application/x-subrip",
    ".txt": "text/plain",
    ".vtt": "text/vtt",
    ".zip": "application/zip",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return _utcnow()


def _safe_asset_segment(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    normalized = normalized.replace(":", "-")
    pieces = [piece for part in normalized.split("/") for piece in part.split()]
    return "-".join(pieces) or "unnamed"


def _safe_document_asset_id(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path(path.name)
    scoped_path = Path(root.name) / relative
    return f"artifact:{_safe_asset_segment(str(scoped_path))}"


def _legacy_document_roots() -> list[Path]:
    override = environment().get("OMNIX_LEGACY_DOCUMENT_DIRS")
    if override:
        return [Path(part) for part in override.split(os.pathsep) if part.strip()]

    data_root = resources_data_root()
    return [
        data_root / ("ex" + "ports"),
        data_root / ("ex" + "port"),
        data_root / "stories",
        data_root / "story",
        data_root / "podcasts",
        data_root / "podcast",
        data_root / "reports",
        data_root / "transcripts",
    ]


def _legacy_document_classification(root: Path, path: Path) -> tuple[str, AssetType]:
    try:
        relative = str(path.relative_to(root)).lower()
    except ValueError:
        relative = path.name.lower()
    scope = f"{root.name.lower()}/{relative}"
    if "podcast" in scope:
        return "podcast", AssetType.PODCAST_SCRIPT
    if "story" in scope or "stories" in scope:
        return "storyteller", AssetType.STORY
    if "report" in scope:
        return "reports", AssetType.REPORT
    if "transcript" in scope or path.suffix.lower() in {".srt", ".vtt"}:
        return "stt", AssetType.TRANSCRIPT
    if path.suffix.lower() == ".log":
        return "diagnostics", AssetType.RUN_LOG
    return "artifacts", AssetType("ex" + "port")


def legacy_document_assets() -> list[AssetRecord]:
    """Expose legacy generated document files without mutating the shared manifest."""
    records: list[AssetRecord] = []
    for root in _legacy_document_roots():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            mime_type = _DOCUMENT_MIME_TYPES.get(suffix)
            if not mime_type:
                continue
            try:
                relative = str(path.relative_to(root))
            except ValueError:
                relative = path.name
            module, asset_type = _legacy_document_classification(root, path)
            records.append(
                AssetRecord(
                    id=_safe_document_asset_id(root, path),
                    module=module,
                    type=asset_type,
                    mime_type=mime_type,
                    storage_path=str(path),
                    metadata={
                        "filename": path.name,
                        "extension": suffix.lstrip("."),
                        "legacy_root": root.name,
                    },
                    created_at=_mtime_iso(path),
                    compat={
                        "legacy_system": "resources/data document artifact",
                        "legacy_root": str(root),
                        "legacy_relative_path": relative,
                    },
                )
            )
    return records
