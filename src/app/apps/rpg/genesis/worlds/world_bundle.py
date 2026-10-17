"""Portable, checksummed archives for reusable RPG worlds and their assets."""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

WORLD_BUNDLE_FORMAT = "omnix_rpg_world_bundle"
WORLD_BUNDLE_VERSION = 1
WORLD_BUNDLE_DATA_PATH = "world.json"
WORLD_BUNDLE_MANIFEST_PATH = "manifest.json"
MAX_WORLD_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_WORLD_BUNDLE_FILE_BYTES = 128 * 1024 * 1024
MAX_WORLD_BUNDLE_ENTRIES = 2_000
MAX_WORLD_BUNDLE_ASSETS = 1_000

_IMAGE_MIME_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
}
_ASSET_REFERENCE_KEYS = {
    "asset_id",
    "asset_ids",
    "image_asset_id",
    "image_asset_ids",
    "background_asset_id",
    "portrait_asset_id",
    "scene_asset_id",
    "thumbnail_asset_id",
}
_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class FrozenBundleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorldBundleAsset(FrozenBundleModel):
    asset_id: str = Field(min_length=1)
    archive_path: str = Field(min_length=1)
    module: str = Field(min_length=1)
    asset_type: str = "image"
    mime_type: str = Field(min_length=1)
    byte_size: int = Field(ge=1)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_job_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    compat: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_image(self) -> "WorldBundleAsset":
        if self.asset_type != "image":
            raise ValueError("world_bundle_asset_must_be_image")
        if self.mime_type.lower() not in _IMAGE_MIME_EXTENSIONS:
            raise ValueError("world_bundle_asset_mime_unsupported")
        _require_safe_archive_path(self.archive_path)
        return self


class WorldBundleManifest(FrozenBundleModel):
    format: str = WORLD_BUNDLE_FORMAT
    version: int = WORLD_BUNDLE_VERSION
    exported_at: str
    source_world_id: str = Field(min_length=1)
    data_path: str = WORLD_BUNDLE_DATA_PATH
    data_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    assets: tuple[WorldBundleAsset, ...] = ()

    @model_validator(mode="after")
    def validate_manifest(self) -> "WorldBundleManifest":
        if self.format != WORLD_BUNDLE_FORMAT or self.version != WORLD_BUNDLE_VERSION:
            raise ValueError("world_bundle_version_unsupported")
        if self.data_path != WORLD_BUNDLE_DATA_PATH:
            raise ValueError("world_bundle_data_path_invalid")
        if len(self.assets) > MAX_WORLD_BUNDLE_ASSETS:
            raise ValueError("world_bundle_asset_limit_exceeded")
        ids = [asset.asset_id for asset in self.assets]
        paths = [asset.archive_path for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("world_bundle_duplicate_asset_id")
        if len(paths) != len(set(paths)):
            raise ValueError("world_bundle_duplicate_asset_path")
        return self


class WorldBundlePayload(FrozenBundleModel):
    world: dict[str, Any]
    topics: tuple[dict[str, Any], ...] = ()
    topic_history: tuple[dict[str, Any], ...] = ()
    generation_runs: tuple[dict[str, Any], ...] = ()
    map_blueprints: tuple[dict[str, Any], ...] = ()
    world_revisions: tuple[dict[str, Any], ...] = ()
    map_definitions: tuple[dict[str, Any], ...] = ()
    world_releases: tuple[dict[str, Any], ...] = ()
    scenarios: tuple[dict[str, Any], ...] = ()
    scenario_revisions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class WorldBundleArchive:
    filename: str
    content: bytes
    manifest: WorldBundleManifest


@dataclass(frozen=True)
class ParsedWorldBundle:
    manifest: WorldBundleManifest
    payload: WorldBundlePayload
    asset_bytes: dict[str, bytes]
    bundle_sha256: str


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json_bytes(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_bundle_segment(value: str) -> str:
    normalized = _SAFE_SEGMENT_RE.sub("-", str(value).strip()).strip("-._")
    return normalized or "world"


def image_extension(mime_type: str) -> str:
    try:
        return _IMAGE_MIME_EXTENSIONS[mime_type.lower()]
    except KeyError as exc:
        raise ValueError(f"world_bundle_asset_mime_unsupported:{mime_type}") from exc


def asset_archive_path(asset_id: str, mime_type: str) -> str:
    return f"assets/{safe_bundle_segment(asset_id)}{image_extension(mime_type)}"


def discover_image_asset_ids(*values: Any) -> set[str]:
    discovered: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                visit(child, str(child_key).lower())
            return
        if isinstance(value, (list, tuple, set)):
            for child in value:
                visit(child, key)
            return
        if not isinstance(value, str):
            return
        if (
            key in _ASSET_REFERENCE_KEYS
            or key.endswith("_asset_id")
            or key.endswith("_asset_ids")
        ) and value.strip():
            discovered.add(value.strip())
            return
        for match in re.findall(r"(?:image:)[a-zA-Z0-9:._/-]+", value):
            discovered.add(match.rstrip(".,);]"))

    for root in values:
        visit(root)
    return discovered


def replace_identifiers(value: Any, replacements: Mapping[str, str]) -> Any:
    ordered = sorted(
        ((str(source), str(target)) for source, target in replacements.items() if source != target),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    if isinstance(value, Mapping):
        return {
            replace_identifiers(key, replacements): replace_identifiers(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [replace_identifiers(child, replacements) for child in value]
    if isinstance(value, tuple):
        return tuple(replace_identifiers(child, replacements) for child in value)
    if isinstance(value, str):
        result = value
        for source, target in ordered:
            result = result.replace(source, target)
        return result
    return value


def build_world_bundle_archive(
    payload: WorldBundlePayload,
    assets: Iterable[tuple[WorldBundleAsset, bytes]],
    *,
    exported_at: str | None = None,
) -> WorldBundleArchive:
    payload_bytes = canonical_json_bytes(payload)
    asset_rows = sorted(list(assets), key=lambda item: item[0].asset_id)
    for asset, content in asset_rows:
        if len(content) != asset.byte_size or sha256_hex(content) != asset.checksum_sha256:
            raise ValueError(f"world_bundle_asset_checksum_mismatch:{asset.asset_id}")
    manifest = WorldBundleManifest(
        exported_at=exported_at or utcnow_iso(),
        source_world_id=str(payload.world.get("id") or ""),
        data_sha256=sha256_hex(payload_bytes),
        assets=tuple(asset for asset, _ in asset_rows),
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(WORLD_BUNDLE_MANIFEST_PATH, canonical_json_bytes(manifest))
        archive.writestr(WORLD_BUNDLE_DATA_PATH, payload_bytes)
        for asset, content in asset_rows:
            archive.writestr(asset.archive_path, content)
    content = output.getvalue()
    if len(content) > MAX_WORLD_BUNDLE_BYTES:
        raise ValueError("world_bundle_size_limit_exceeded")
    filename = f"{safe_bundle_segment(manifest.source_world_id)}.omnix-world.zip"
    return WorldBundleArchive(filename=filename, content=content, manifest=manifest)


def parse_world_bundle_archive(content: bytes) -> ParsedWorldBundle:
    if not content or len(content) > MAX_WORLD_BUNDLE_BYTES:
        raise ValueError("world_bundle_size_limit_exceeded")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content), mode="r")
    except zipfile.BadZipFile as exc:
        raise ValueError("world_bundle_zip_invalid") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_WORLD_BUNDLE_ENTRIES:
            raise ValueError("world_bundle_entry_limit_exceeded")
        by_name = {info.filename: info for info in infos}
        if WORLD_BUNDLE_MANIFEST_PATH not in by_name or WORLD_BUNDLE_DATA_PATH not in by_name:
            raise ValueError("world_bundle_required_entry_missing")
        total_uncompressed = 0
        for info in infos:
            _require_safe_archive_path(info.filename)
            if info.file_size > MAX_WORLD_BUNDLE_FILE_BYTES:
                raise ValueError(f"world_bundle_entry_too_large:{info.filename}")
            total_uncompressed += info.file_size
        if total_uncompressed > MAX_WORLD_BUNDLE_BYTES:
            raise ValueError("world_bundle_uncompressed_size_limit_exceeded")
        manifest = WorldBundleManifest.model_validate_json(
            archive.read(WORLD_BUNDLE_MANIFEST_PATH)
        )
        payload_bytes = archive.read(manifest.data_path)
        if sha256_hex(payload_bytes) != manifest.data_sha256:
            raise ValueError("world_bundle_data_checksum_mismatch")
        payload = WorldBundlePayload.model_validate_json(payload_bytes)
        if str(payload.world.get("id") or "") != manifest.source_world_id:
            raise ValueError("world_bundle_source_world_mismatch")
        asset_bytes: dict[str, bytes] = {}
        for asset in manifest.assets:
            if asset.archive_path not in by_name:
                raise ValueError(f"world_bundle_asset_missing:{asset.asset_id}")
            data = archive.read(asset.archive_path)
            if len(data) != asset.byte_size or sha256_hex(data) != asset.checksum_sha256:
                raise ValueError(f"world_bundle_asset_checksum_mismatch:{asset.asset_id}")
            asset_bytes[asset.asset_id] = data
    return ParsedWorldBundle(
        manifest=manifest,
        payload=payload,
        asset_bytes=asset_bytes,
        bundle_sha256=sha256_hex(content),
    )


def _require_safe_archive_path(value: str) -> None:
    path = PurePosixPath(str(value))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"world_bundle_archive_path_invalid:{value}")
