from __future__ import annotations

from app.caching.bounded_cache import bounded_lru_cache

from pathlib import Path
from typing import Any

from app.assets.content import materialize_asset
from app.assets.models import AssetListResponse, AssetMigrationPreview, AssetRecord
from app.assets.models import AssetContentTooLarge

from .blob_store import default_blob_store
from .contracts import BlobStore
from .database import PostgresDatabase, default_database
from app.runtime.pagination import decode_cursor, encode_cursor, page_limit
from app.runtime.tenant_context import RequestTenant
from .unit_of_work import unit_of_work


class PostgresSharedAssetStoreAdapter:
    context = RequestTenant()
    def __init__(
        self,
        manifest_path: str | Path | None = None,
        *,
        database: PostgresDatabase | None = None,
        blob_store: BlobStore | None = None,
    ) -> None:
        if manifest_path is not None:
            raise RuntimeError(
                "manifest-backed asset authority is retired; use the Phase 8 importer"
            )
        self.database = database or default_database()
        self.context = None  # follows the request tenant
        self.blob_store = blob_store or default_blob_store()

    def list_assets(
        self,
        *,
        asset_type: str | None = None,
        modules: tuple[str, ...] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> AssetListResponse:
        """One page, newest first; follow ``next_cursor`` for the rest (WP-5.5)."""
        size = page_limit(limit)
        before = decode_cursor(cursor, arity=2)
        with unit_of_work(self.database) as work:
            records = work.assets.list_assets(
                self.context,
                asset_type=asset_type,
                modules=modules,
                limit=size + 1,
                before_created_at=before[0] if before else None,
                before_id=before[1] if before else None,
            )
            work.rollback()
        page = records[:size]
        has_more = len(records) > size
        return AssetListResponse(
            assets=[self._asset(record) for record in page],
            next_cursor=encode_cursor(page[-1]["created_at"], page[-1]["id"]) if has_more else None,
            has_more=has_more,
        )

    def get_asset(self, asset_id: str) -> AssetRecord | None:
        with unit_of_work(self.database) as work:
            record = work.assets.get_asset(self.context, str(asset_id))
            work.rollback()
        if record is None or record["lifecycle_status"] == "deleted":
            return None
        return self._asset(record)

    def read_asset_bytes(self, asset_id: str, *, max_bytes: int) -> bytes:
        with unit_of_work(self.database) as work:
            record = work.assets.get_asset(self.context, str(asset_id))
            work.rollback()
        if record is None or record["lifecycle_status"] == "deleted":
            raise FileNotFoundError(asset_id)
        if int(record["byte_size"]) > max_bytes:
            raise AssetContentTooLarge(asset_id)
        return self.blob_store.read_bytes(
            record["storage_key"],
            expected_checksum=record["checksum_sha256"],
        )

    def upsert_asset(self, asset: AssetRecord) -> AssetRecord:
        """Create an asset, or update an existing one's descriptor in one transaction."""
        with unit_of_work(self.database) as work:
            record = work.assets.update_descriptor(
                self.context,
                asset.id,
                module=asset.module,
                asset_type=self._enum_value(asset.type),
                mime_type=asset.mime_type,
                metadata=dict(asset.metadata),
                compat=dict(asset.compat),
                generation_job_id=asset.source_job_id,
            )
            work.commit()
        if record is not None:
            return self._asset(record)

        source = materialize_asset(asset)  # FileNotFoundError when absent
        storage_key = f"assets/{self._safe(asset.id)}/{source.name}"
        blob = self.blob_store.put_file(storage_key, source)
        try:
            with unit_of_work(self.database) as work:
                record = work.assets.create(
                    self.context,
                    {
                        "id": asset.id,
                        "module": asset.module,
                        "asset_type": self._enum_value(asset.type),
                        "mime_type": asset.mime_type,
                        "byte_size": blob["byte_size"],
                        "checksum_sha256": blob["checksum_sha256"],
                        "storage_provider": blob["storage_provider"],
                        "storage_key": blob["storage_key"],
                        "owner_user_id": asset.owner_id,
                        "generation_job_id": asset.source_job_id,
                        "metadata": dict(asset.metadata),
                        "compat": {
                            **dict(asset.compat),
                            "legacy_source_path": str(source),
                        },
                    },
                )
                work.commit()
        except Exception:
            if blob.get("created"):
                self.blob_store.delete(storage_key)
            raise
        return self._asset(record)

    def delete_asset(self, asset_id: str, *, delete_file: bool = True) -> dict[str, Any]:
        with unit_of_work(self.database) as work:
            record = work.assets.get_asset(self.context, asset_id)
            if record is None:
                work.rollback()
                return {
                    "ok": False,
                    "asset_id": asset_id,
                    "deleted": False,
                    "file_deleted": False,
                }
            deleted = work.assets.mark_deleted(
                self.context,
                asset_id=asset_id,
                expected_revision=record["revision"],
            )
            work.commit()
        file_deleted = self.blob_store.delete(record["storage_key"]) if delete_file else False
        return {
            "ok": True,
            "asset_id": asset_id,
            "deleted": deleted["lifecycle_status"] == "deleted",
            "file_deleted": file_deleted,
        }

    def preview_image_manifest_import(
        self,
        image_manifest: dict[str, Any] | None = None,
    ) -> AssetMigrationPreview:
        del image_manifest
        return AssetMigrationPreview(
            source="retired-manifest-authority",
            would_import=0,
            missing_files=[],
            assets=[],
        )

    def import_image_manifest_dry_run(
        self,
        image_manifest: dict[str, Any] | None = None,
    ) -> AssetMigrationPreview:
        return self.preview_image_manifest_import(image_manifest)

    def import_image_manifest(
        self,
        image_manifest: dict[str, Any] | None = None,
    ) -> AssetMigrationPreview:
        raise RuntimeError(
            "runtime manifest import is retired; use scripts/import_legacy_persistence_bundle.py"
        )

    def _asset(self, record: dict[str, Any]) -> AssetRecord:
        local_path = getattr(self.blob_store, "local_path", None)
        return AssetRecord(
            id=record["id"],
            owner_id=record.get("owner_user_id"),
            source_job_id=record.get("generation_job_id"),
            module=record["module"],
            type=record["asset_type"],
            mime_type=record["mime_type"],
            # Kept for local deployments' API compatibility; S3 has no path.
            storage_path=str(local_path(record["storage_key"])) if callable(local_path) else "",
            storage_key=record["storage_key"],
            checksum_sha256=record.get("checksum_sha256"),
            metadata=dict(record.get("metadata") or {}),
            created_at=record["created_at"],
            compat=dict(record.get("compat") or {}),
        )

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(getattr(value, "value", value))

    @staticmethod
    def _safe(value: str) -> str:
        import re

        return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "asset"

    @staticmethod
    def _json(value: Any) -> str:
        import json

        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def production_asset_store() -> PostgresSharedAssetStoreAdapter:
    return PostgresSharedAssetStoreAdapter()
