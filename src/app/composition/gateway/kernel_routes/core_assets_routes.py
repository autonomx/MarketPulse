"""Core assets routes with explicitly injected store factories."""

from __future__ import annotations

from app.assets.protocol import AssetStore
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.assets import (
    AssetLegacyImportDryRun,
    AssetListResponse,
    AssetMigrationPreview,
    AssetRecord,
    AssetType,
    PublicAssetLegacyImportDryRun,
    PublicAssetListResponse,
    PublicAssetMigrationPreview,
    PublicAssetRecord,
)
from app.runtime.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, InvalidCursor
from app.assets.content import AssetContentUnavailable, materialize_asset
from app.composition.gateway.schemas import AssetContentResponse
from app.assets.models import AssetContentTooLarge


def _asset_by_id(asset_store: AssetStore, asset_id: str) -> AssetRecord | None:
    return asset_store.get_asset(asset_id)


def _text_asset_supported(asset: AssetRecord) -> bool:
    mime_type = asset.mime_type.lower().split(";", 1)[0]
    return mime_type.startswith("text/") or mime_type in {
        "application/json",
        "application/x-subrip",
        "text/html",
        "text/markdown",
        "text/plain",
        "text/vtt",
    }


def _read_text_asset(asset_store: AssetStore, asset: AssetRecord) -> AssetContentResponse:
    if not _text_asset_supported(asset):
        raise HTTPException(status_code=415, detail="asset_content_not_text")
    try:
        content_bytes = asset_store.read_asset_bytes(asset.id, max_bytes=2_000_000)
    except AssetContentTooLarge as exc:
        raise HTTPException(status_code=413, detail="asset_content_too_large") from exc
    except OSError as exc:
        raise HTTPException(status_code=404, detail="asset_file_not_found") from exc
    try:
        content = content_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail="asset_content_not_utf8") from exc
    size_bytes = len(content_bytes)
    return AssetContentResponse(asset=PublicAssetRecord.model_validate(asset), content=content, size_bytes=size_bytes)


def register_core_assets_routes(router: APIRouter, *, get_asset_store):
    @router.get("/api/assets", response_model=PublicAssetListResponse, tags=["assets"])
    def assets(
        asset_type: Annotated[AssetType | None, Query(alias="type")] = None,
        module: str | None = Query(default=None, max_length=100),
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        cursor: str | None = Query(default=None, max_length=512),
    ) -> AssetListResponse:
        """One page of assets, newest first; follow ``next_cursor`` (WP-5.5)."""
        try:
            return get_asset_store().list_assets(
                asset_type=asset_type.value if asset_type is not None else None,
                modules=(module,) if module else None,
                limit=limit,
                cursor=cursor,
            )
        except InvalidCursor as error:
            raise HTTPException(status_code=400, detail="invalid_cursor") from error

    @router.get(
        "/api/assets/{asset_id}/content",
        response_model=AssetContentResponse,
    )
    def asset_content(asset_id: str) -> AssetContentResponse:
        asset_store = get_asset_store()
        asset = _asset_by_id(asset_store, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset_not_found")
        return _read_text_asset(asset_store, asset)

    @router.get("/api/assets/{asset_id}/audio", response_class=FileResponse, tags=["assets"])
    def asset_audio(asset_id: str) -> FileResponse:
        """Stream a stored audio asset (with Range support) from any blob backend."""
        asset_store = get_asset_store()
        asset = _asset_by_id(asset_store, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset_not_found")
        mime_type = asset.mime_type.lower().split(";", 1)[0]
        if not mime_type.startswith("audio/"):
            raise HTTPException(status_code=415, detail="asset_content_not_audio")
        try:
            path = materialize_asset(asset)
        except AssetContentUnavailable as exc:
            raise HTTPException(status_code=404, detail="asset_file_not_found") from exc
        return FileResponse(
            path,
            media_type=mime_type,
            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )

    @router.get("/api/assets/{asset_id}/download", response_class=FileResponse, tags=["assets"])
    def asset_download(asset_id: str) -> FileResponse:
        """The asset's bytes as an attachment, by id: clients never learn where they are stored (WP-4.10)."""
        asset = _asset_by_id(get_asset_store(), asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset_not_found")
        try:
            path = materialize_asset(asset)
        except AssetContentUnavailable as exc:
            raise HTTPException(status_code=404, detail="asset_file_not_found") from exc
        return FileResponse(
            path,
            media_type=asset.mime_type or "application/octet-stream",
            filename=PublicAssetRecord.model_validate(asset).file_name or "asset",
            headers={
                "Cache-Control": "private, no-cache",
                "Content-Security-Policy": "sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post(
        "/api/assets/migrations/image/dry-run",
        response_model=PublicAssetMigrationPreview,
        tags=["assets"],
    )
    def image_asset_migration_dry_run() -> AssetMigrationPreview:
        return get_asset_store().import_image_manifest_dry_run()

    @router.post(
        "/api/assets/migrations/image/import",
        response_model=PublicAssetMigrationPreview,
        tags=["assets"],
    )
    def image_asset_migration_import() -> AssetMigrationPreview:
        return get_asset_store().import_image_manifest()

    @router.post(
        "/api/assets/migrations/legacy-non-image/dry-run",
        response_model=PublicAssetLegacyImportDryRun,
        tags=["assets"],
    )
    def legacy_non_image_asset_migration_dry_run() -> AssetLegacyImportDryRun:
        return get_asset_store().preview_legacy_non_image_import()
