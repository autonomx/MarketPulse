"""Shared asset/artifact library."""
from __future__ import annotations

from .protocol import AssetStore
import logging
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .content import asset_location, open_asset
from .canonical_voice_clones import (
    canonical_voice_clone_root,
    discover_canonical_voice_clone_assets,
)
from .legacy_documents import (
    _DOCUMENT_MIME_TYPES,
    _legacy_document_roots,
    legacy_document_assets,
)
from .models import (
    AssetLegacyImportDryRun,
    AssetLegacyRootScan,
    AssetListResponse,
    AssetMigrationPreview,
    AssetRecord,
    AssetType,
    PublicAssetLegacyImportDryRun,
    PublicAssetListResponse,
    PublicAssetMigrationPreview,
    PublicAssetRecord,
    asset_download_url,
)
from .paging import iter_assets, paginate_assets
from .rpg_map_pack import curated_rpg_map_assets
from .store import (
    _AUDIO_MIME_TYPES,
    _legacy_audio_roots,
    SharedAssetStore as ManifestSharedAssetStore,
)
from .voice_clone_assets import discover_voice_clone_assets, voice_clone_sources

LOGGER = logging.getLogger("uvicorn.error")


def _root_scan(family: str, path: Path) -> AssetLegacyRootScan:
    return AssetLegacyRootScan(family=family, path=str(path), exists=path.is_dir() or path.is_file())


def _skipped_files(family: str, roots: list[Path], supported_suffixes: set[str]) -> list[dict[str, Any]]:
    skipped: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in supported_suffixes:
                continue
            skipped.append(
                {
                    "family": family,
                    "path": str(path),
                    "extension": suffix.lstrip("."),
                    "reason": "unsupported_file_type",
                }
            )
    return skipped


def _merge_assets(
    assets: dict[str, AssetRecord],
    source: str,
    loader: Callable[[], Iterable[AssetRecord]],
) -> int:
    """Merge one compatibility source without allowing it to break the library."""

    before = len(assets)
    try:
        candidates = list(loader())
        for asset in candidates:
            assets.setdefault(asset.id, asset)
    except Exception:
        LOGGER.exception("[Voice Library][assets] source failed source=%s", source)
        return 0

    added = len(assets) - before
    LOGGER.info(
        "[Voice Library][assets] source loaded source=%s candidates=%d added=%d",
        source,
        len(candidates),
        added,
    )
    return added


def _voice_debug_rows(assets: Iterable[AssetRecord]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for asset in assets:
        if asset.type != AssetType.VOICE_PROFILE and asset.module != "voice-cloning":
            continue
        metadata = dict(asset.metadata or {})
        rows.append(
            {
                "id": asset.id,
                "name": str(
                    metadata.get("profile_name")
                    or metadata.get("voice_id")
                    or metadata.get("speaker")
                    or asset.id
                ),
                "path": asset_location(asset),
            }
        )
    return rows


class SharedAssetStore(ManifestSharedAssetStore):
    """Shared asset store with non-mutating compatibility and curated read-through."""

    def _legacy_voice_clone_assets(self) -> list[AssetRecord]:
        """Read voice profiles from configured and compatibility directories."""
        return discover_voice_clone_assets()

    def list_assets(
        self,
        *,
        asset_type: str | None = None,
        modules: tuple[str, ...] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> AssetListResponse:
        # The shared manifest remains authoritative, but each compatibility source
        # is isolated. The canonical clone directory is scanned independently so an
        # environment override can never hide resources/voice_clones.
        LOGGER.info(
            "[Voice Library][assets] list started cwd=%s manifest=%s store=%s",
            os.getcwd(),
            self.manifest_path,
            type(self).__name__,
        )
        assets = dict(super()._load_manifest())
        LOGGER.info(
            "[Voice Library][assets] shared manifest loaded count=%d",
            len(assets),
        )
        _merge_assets(
            assets,
            "canonical_voice_clones",
            discover_canonical_voice_clone_assets,
        )
        _merge_assets(assets, "voice_clone_compatibility", self._legacy_voice_clone_assets)
        _merge_assets(assets, "generated_audio", super()._legacy_audio_assets)
        _merge_assets(
            assets,
            "image_manifest",
            lambda: self.preview_image_manifest_import().assets,
        )
        _merge_assets(assets, "legacy_documents", legacy_document_assets)
        _merge_assets(assets, "curated_rpg_maps", curated_rpg_map_assets)

        voice_rows = _voice_debug_rows(assets.values())
        LOGGER.info(
            "[Voice Library][assets] list completed total=%d voice_profiles=%d voices=%s",
            len(assets),
            len(voice_rows),
            voice_rows[:50],
        )
        return paginate_assets(
            assets.values(), asset_type=asset_type, modules=modules, limit=limit, cursor=cursor
        )

    def get_asset(self, asset_id: str) -> AssetRecord | None:
        normalized_id = str(asset_id)
        manifest_asset = super()._load_manifest().get(normalized_id)
        if manifest_asset is not None:
            return manifest_asset

        candidates: dict[str, AssetRecord] = {}
        _merge_assets(
            candidates,
            "canonical_voice_clones",
            discover_canonical_voice_clone_assets,
        )
        _merge_assets(candidates, "voice_clone_compatibility", self._legacy_voice_clone_assets)
        _merge_assets(candidates, "generated_audio", super()._legacy_audio_assets)
        _merge_assets(
            candidates,
            "image_manifest",
            lambda: self.preview_image_manifest_import().assets,
        )
        _merge_assets(candidates, "legacy_documents", legacy_document_assets)
        _merge_assets(candidates, "curated_rpg_maps", curated_rpg_map_assets)
        return candidates.get(normalized_id)

    def read_asset_bytes(self, asset_id: str, *, max_bytes: int) -> bytes:
        """Read a small legacy asset without exposing its storage path to callers."""
        asset = self.get_asset(asset_id)
        if asset is None:
            raise FileNotFoundError(asset_id)
        with open_asset(asset) as handle:
            content = handle.read(max_bytes + 1)
        if len(content) > max_bytes:
            from app.assets.models import AssetContentTooLarge

            raise AssetContentTooLarge(asset_id)
        return content

    def preview_legacy_non_image_import(self) -> AssetLegacyImportDryRun:
        """Summarize non-image legacy assets without mutating any source."""
        manifest_assets = super()._load_manifest()
        voice_assets: dict[str, AssetRecord] = {}
        _merge_assets(
            voice_assets,
            "canonical_voice_clones",
            discover_canonical_voice_clone_assets,
        )
        _merge_assets(voice_assets, "voice_clone_compatibility", self._legacy_voice_clone_assets)
        legacy_assets = [
            *voice_assets.values(),
            *super()._legacy_audio_assets(),
            *legacy_document_assets(),
        ]

        category_counts: dict[str, int] = {}
        for asset in legacy_assets:
            category_counts[asset.type.value] = category_counts.get(asset.type.value, 0) + 1

        collision_asset_ids = sorted({asset.id for asset in legacy_assets if asset.id in manifest_assets})
        import_candidates = [asset for asset in legacy_assets if asset.id not in manifest_assets]

        audio_roots = _legacy_audio_roots()
        document_roots = _legacy_document_roots()
        roots_scanned = [
            *_voice_clone_roots(),
            *[_root_scan("audio", root) for root in audio_roots],
            *[_root_scan("documents", root) for root in document_roots],
        ]
        skipped_files = [
            *_skipped_files("audio", audio_roots, set(_AUDIO_MIME_TYPES)),
            *_skipped_files("documents", document_roots, set(_DOCUMENT_MIME_TYPES)),
        ]
        warnings = []
        if collision_asset_ids:
            warnings.append(
                f"{len(collision_asset_ids)} legacy asset id(s) already exist in the shared manifest; manifest records remain authoritative."
            )

        return AssetLegacyImportDryRun(
            source="legacy non-image read-through assets",
            would_import=len(import_candidates),
            category_counts=dict(sorted(category_counts.items())),
            roots_scanned=roots_scanned,
            collision_asset_ids=collision_asset_ids,
            skipped_files=skipped_files,
            warnings=warnings,
            assets=import_candidates,
        )


def _voice_clone_roots() -> list[AssetLegacyRootScan]:
    roots: list[AssetLegacyRootScan] = []
    seen: set[tuple[str, str]] = set()
    canonical_root = canonical_voice_clone_root()
    source_rows = [(canonical_root, canonical_root / "voice_clones.json"), *voice_clone_sources()]
    for clones_dir, clones_file in source_rows:
        for family, path in (
            ("voice_cloning", clones_dir),
            ("voice_cloning_manifest", clones_file),
        ):
            key = (family, str(path))
            if key in seen:
                continue
            seen.add(key)
            roots.append(_root_scan(family, path))
    return roots


def default_asset_store() -> AssetStore:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.persistence.shared_asset_store import production_asset_store
        return production_asset_store()
    return SharedAssetStore()


__all__ = [
    "AssetListResponse",
    "AssetLegacyImportDryRun",
    "AssetLegacyRootScan",
    "AssetMigrationPreview",
    "PublicAssetLegacyImportDryRun",
    "PublicAssetListResponse",
    "PublicAssetMigrationPreview",
    "PublicAssetRecord",
    "asset_download_url",
    "AssetRecord",
    "AssetType",
    "SharedAssetStore",
    "default_asset_store",
    "iter_assets",
    "paginate_assets",
]
