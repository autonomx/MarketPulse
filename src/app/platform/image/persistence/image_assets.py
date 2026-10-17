"""PostgreSQL/BlobStore compatibility functions for generated image assets."""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.assets.content import asset_location
from app.assets.models import AssetRecord, AssetType
from app.assets.paging import iter_assets

from app.persistence.shared_asset_store import PostgresSharedAssetStoreAdapter


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_image_asset_bytes_postgres(
    image_bytes: bytes,
    mime_type: str,
    asset_id: str,
    metadata: dict[str, Any],
) -> str:
    suffix = ".png" if "png" in mime_type.casefold() else ".image"
    with tempfile.TemporaryDirectory(prefix="omnix-image-asset-") as directory:
        path = Path(directory) / f"{_safe(asset_id)}-{hashlib.sha256(image_bytes).hexdigest()[:16]}{suffix}"
        path.write_bytes(image_bytes)
        stored = PostgresSharedAssetStoreAdapter().upsert_asset(
            AssetRecord(
                id=str(asset_id),
                module="image",
                type=AssetType.IMAGE,
                mime_type=mime_type,
                storage_path=str(path),
                metadata=dict(metadata),
                compat={"source": "image_asset_compat"},
                created_at=_now(),
            )
        )
    return asset_location(stored)


def register_image_asset_file_postgres(
    file_path: str,
    asset_id: str,
    metadata: dict[str, Any],
) -> str:
    stored = PostgresSharedAssetStoreAdapter().upsert_asset(
        AssetRecord(
            id=str(asset_id),
            module="image",
            type=AssetType.IMAGE,
            mime_type="image/png",
            storage_path=str(file_path),
            metadata=dict(metadata),
            compat={"source": "image_asset_compat"},
            created_at=_now(),
        )
    )
    return asset_location(stored)


def get_image_asset_manifest_postgres() -> dict[str, Any]:
    store = PostgresSharedAssetStoreAdapter()
    assets = {
        asset.id: asset
        for asset in (
            *iter_assets(store, asset_type=AssetType.IMAGE.value),
            *iter_assets(store, modules=("image",)),
        )
    }.values()
    return {
        "format_version": "postgresql_image_assets_v1",
        "assets": {
            item.id: {
                "path": asset_location(item),
                "mime_type": item.mime_type,
                "hash": str(item.checksum_sha256 or item.compat.get("checksum_sha256") or ""),
                "metadata": dict(item.metadata),
            }
            for item in assets
            if item.module == "image" or str(item.type) == str(AssetType.IMAGE.value)
        },
    }


def delete_image_asset_postgres(
    asset_id: str,
    *,
    delete_file: bool = True,
) -> dict[str, Any]:
    return PostgresSharedAssetStoreAdapter().delete_asset(
        str(asset_id),
        delete_file=delete_file,
    )


def cleanup_unused_image_assets_postgres() -> dict[str, Any]:
    # Blob lifecycle cleanup is metadata-driven and must not scan/delete arbitrary
    # files from the configured root. Orphan sweeps are a separate operator job.
    return {
        "ok": True,
        "backend": "postgresql_blob_store",
        "deleted": 0,
        "reason": "metadata_driven_cleanup",
    }


def _safe(value: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-") or "asset"
