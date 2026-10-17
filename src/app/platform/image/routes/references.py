"""Browser routes for reusable image-to-image reference assets."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel

from app.assets import AssetListResponse, PublicAssetListResponse, PublicAssetRecord
from app.runtime.contracts import AssetService
from app.platform.image.reference_assets import (
    ImageReferenceError,
    list_image_reference_assets,
    save_image_reference_upload,
)

DEFAULT_REFERENCE_LIMIT = 100
MAX_REFERENCE_LIMIT = 250


class ImageReferenceUploadResponse(BaseModel):
    ok: bool
    asset: PublicAssetRecord


def create_image_reference_router(asset_store: AssetService) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/image-generation/references",
        response_model=PublicAssetListResponse,
        tags=["image"],
    )
    def image_references(
        limit: int = Query(default=DEFAULT_REFERENCE_LIMIT, ge=1, le=MAX_REFERENCE_LIMIT),
    ) -> AssetListResponse:
        return list_image_reference_assets(limit=limit, store=asset_store)

    @router.post(
        "/api/image-generation/references",
        response_model=ImageReferenceUploadResponse,
        tags=["image"],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                    "image/png": {"schema": {"type": "string", "format": "binary"}},
                    "image/webp": {"schema": {"type": "string", "format": "binary"}},
                },
            }
        },
        responses={422: {"description": "The upload is empty, invalid, or unsupported"}},
    )
    def upload_image_reference(
        request: Request,
        image_content: bytes = Body(..., media_type="application/octet-stream"),
        filename: str = Query(default="reference-image"),
    ) -> ImageReferenceUploadResponse:
        try:
            asset = save_image_reference_upload(
                image_content,
                filename=filename,
                mime_type=request.headers.get("content-type", ""),
                store=asset_store,
            )
        except ImageReferenceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ImageReferenceUploadResponse(ok=True, asset=PublicAssetRecord.model_validate(asset))

    return router
