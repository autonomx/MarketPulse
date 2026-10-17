"""Shared asset model for generated and imported Omnix artifacts."""
from __future__ import annotations

import re
from enum import Enum
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, Field, model_validator


class AssetType(str, Enum):
    AUDIO = "audio"
    VOICE_SAMPLE = "voice_sample"
    VOICE_PROFILE = "voice_profile"
    IMAGE = "image"
    TRANSCRIPT = "transcript"
    STORY = "story"
    PODCAST_SCRIPT = "podcast_script"
    REPORT = "report"
    RPG_CHECKPOINT = "rpg_checkpoint"
    RUN_LOG = "run_log"
    EXPORT = "export"
    SETTINGS_ARTIFACT = "settings_artifact"
    SOURCE = "source"
    COVER = "cover"
    RENDER = "render"
    CHAPTER_AUDIO = "chapter-audio"
    OTHER = "other"


class AssetRecord(BaseModel):
    id: str
    owner_id: str | None = None
    module: str
    type: AssetType
    mime_type: str
    # Local filesystem path for file-discovered or legacy assets. Blob-backed
    # assets set ``storage_key`` instead (WP-5.8); read either through
    # ``app.assets.content`` rather than opening this path directly.
    storage_path: str = ""
    storage_key: str | None = None
    checksum_sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_job_id: str | None = None
    parent_asset_ids: list[str] = Field(default_factory=list)
    derived_asset_ids: list[str] = Field(default_factory=list)
    created_at: str
    compat: dict[str, Any] = Field(default_factory=dict)


# An absolute filesystem path: a drive (C:\ or C:/), a UNC share, or a POSIX
# path outside the API's own URL space.
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\]|/(?!/|api/)\S*[\\/])")


def _file_name(value: str) -> str:
    return re.split(r"[\\/]", value.rstrip("\\/"))[-1] if value else ""


def _without_paths(value: Any) -> Any:
    """Absolute filesystem paths reduced to their file names, at any depth."""
    if isinstance(value, str):
        return _file_name(value) if _ABSOLUTE_PATH.match(value) else value
    if isinstance(value, dict):
        return {key: _without_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_paths(item) for item in value]
    return value


def asset_download_url(asset_id: str) -> str:
    return f"/api/assets/{quote(asset_id, safe='')}/download"


class PublicAssetRecord(BaseModel):
    """An asset as the API returns it: ids and URLs, never where its bytes are
    stored (WP-4.10). Built from an ``AssetRecord``: the storage path and key
    and the legacy ``compat`` block are left out, absolute paths in metadata
    become file names."""

    id: str
    owner_id: str | None = None
    module: str
    type: AssetType
    mime_type: str
    file_name: str = ""
    download_url: str = ""
    checksum_sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_job_id: str | None = None
    parent_asset_ids: list[str] = Field(default_factory=list)
    derived_asset_ids: list[str] = Field(default_factory=list)
    created_at: str

    @model_validator(mode="before")
    @classmethod
    def _from_record(cls, value: Any) -> Any:
        if isinstance(value, BaseModel):
            value = value.model_dump()
        if not isinstance(value, dict) or not ({"storage_path", "storage_key", "compat"} & value.keys()):
            return value
        public = {key: item for key, item in value.items() if key not in {"storage_path", "storage_key", "compat"}}
        public["file_name"] = _file_name(str(value.get("storage_path") or value.get("storage_key") or ""))
        public["download_url"] = asset_download_url(str(value.get("id") or ""))
        public["metadata"] = _without_paths(value.get("metadata") or {})
        return public


class AssetContentTooLarge(ValueError):
    """Raised when a bounded asset-content read exceeds its caller's limit."""


class AssetListResponse(BaseModel):
    """A page of assets, newest first (WP-5.5)."""

    assets: list[AssetRecord]
    next_cursor: str | None = None
    has_more: bool = False


class PublicAssetListResponse(AssetListResponse):
    """``AssetListResponse`` as the API returns it."""

    assets: list[PublicAssetRecord]  # type: ignore[assignment]


class AssetMigrationPreview(BaseModel):
    source: str
    would_import: int
    missing_files: list[dict[str, Any]] = Field(default_factory=list)
    assets: list[AssetRecord] = Field(default_factory=list)


class AssetLegacyRootScan(BaseModel):
    family: str
    path: str
    exists: bool


class AssetLegacyImportDryRun(BaseModel):
    source: str
    would_import: int
    category_counts: dict[str, int] = Field(default_factory=dict)
    roots_scanned: list[AssetLegacyRootScan] = Field(default_factory=list)
    collision_asset_ids: list[str] = Field(default_factory=list)
    skipped_files: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assets: list[AssetRecord] = Field(default_factory=list)


class PublicAssetMigrationPreview(AssetMigrationPreview):
    assets: list[PublicAssetRecord] = Field(default_factory=list)  # type: ignore[assignment]


class PublicAssetLegacyImportDryRun(AssetLegacyImportDryRun):
    assets: list[PublicAssetRecord] = Field(default_factory=list)  # type: ignore[assignment]
