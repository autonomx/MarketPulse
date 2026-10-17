"""Reference-image storage and loading for image-to-image generation."""
from __future__ import annotations

from app.assets.protocol import AssetStore
import logging

import io
import uuid
from itertools import islice
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.assets.content import AssetContentUnavailable, asset_available, open_asset
from app.assets import AssetListResponse, AssetRecord, AssetType, default_asset_store, iter_assets
from app.runtime.paths import resources_data_root

logger = logging.getLogger(__name__)

REFERENCE_ASSET_MODULE = "image-reference"
SUPPORTED_REFERENCE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_REFERENCE_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_REFERENCE_COUNT = 2
MAX_REFERENCE_EDGE = 1024


class ImageReferenceError(ValueError):
    """Raised when a reference-image request is invalid or unavailable."""


def _pillow():
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise ImageReferenceError("image_reference_runtime_missing:Pillow") from exc
    return Image, ImageOps, UnidentifiedImageError


def image_reference_root() -> Path:
    return resources_data_root() / "image_references"


def list_image_reference_assets(
    *,
    limit: int = 100,
    store: AssetStore | None = None,
) -> AssetListResponse:
    asset_store = store or default_asset_store()
    # Newest first from the store; stop once the page is full.
    usable = (
        asset
        for asset in iter_assets(
            asset_store,
            asset_type=AssetType.IMAGE.value,
            modules=("image", "image-generation", REFERENCE_ASSET_MODULE),
        )
        if _usable_reference_asset(asset)
    )
    return AssetListResponse(assets=list(islice(usable, max(1, int(limit)))))


def save_image_reference_upload(
    data: bytes,
    *,
    filename: str,
    mime_type: str,
    store: AssetStore | None = None,
    root: Path | None = None,
) -> AssetRecord:
    if not data:
        raise ImageReferenceError("image_reference_empty_upload")
    if len(data) > MAX_REFERENCE_UPLOAD_BYTES:
        raise ImageReferenceError(
            f"image_reference_too_large:size_bytes={len(data)} max_bytes={MAX_REFERENCE_UPLOAD_BYTES}"
        )
    normalized_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    if normalized_mime not in SUPPORTED_REFERENCE_MIME_TYPES:
        raise ImageReferenceError(f"image_reference_unsupported_type:{normalized_mime or 'unknown'}")

    Image, ImageOps, UnidentifiedImageError = _pillow()
    try:
        with Image.open(io.BytesIO(data)) as uploaded:
            uploaded.verify()
        with Image.open(io.BytesIO(data)) as uploaded:
            normalized = ImageOps.exif_transpose(uploaded)
            normalized.seek(0)
            normalized = normalized.convert("RGB")
            normalized.thumbnail((MAX_REFERENCE_EDGE, MAX_REFERENCE_EDGE), Image.Resampling.LANCZOS)
            normalized = normalized.copy()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageReferenceError(f"image_reference_invalid_image:{exc}") from exc

    asset_store = store or default_asset_store()
    destination_root = root or image_reference_root()
    destination_root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    destination = destination_root / f"{token}.png"
    normalized.save(destination, format="PNG", optimize=True)

    title = Path(str(filename or "reference-image")).name.strip() or "reference-image"
    asset = AssetRecord(
        id=f"image-reference:{token}",
        module=REFERENCE_ASSET_MODULE,
        type=AssetType.IMAGE,
        mime_type="image/png",
        storage_path=str(destination),
        metadata={
            "title": title,
            "filename": title,
            "width": normalized.width,
            "height": normalized.height,
            "reference_upload": True,
            "original_mime_type": normalized_mime,
        },
        created_at=datetime.now(timezone.utc).isoformat(),
        compat={"uploaded_reference": True},
    )
    try:
        return asset_store.upsert_asset(asset)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def load_image_reference_assets(
    asset_ids: Iterable[str],
    *,
    store: AssetStore | None = None,
) -> list[Any]:
    normalized_ids = _normalize_reference_ids(asset_ids)
    if not normalized_ids:
        return []
    if len(normalized_ids) > MAX_REFERENCE_COUNT:
        raise ImageReferenceError(
            f"image_reference_limit_exceeded:count={len(normalized_ids)} max={MAX_REFERENCE_COUNT}"
        )

    Image, ImageOps, UnidentifiedImageError = _pillow()
    asset_store = store or default_asset_store()
    images: list[Any] = []
    try:
        for asset_id in normalized_ids:
            asset = asset_store.get_asset(asset_id)
            if asset is None:
                raise ImageReferenceError(f"image_reference_not_found:{asset_id}")
            if asset.type != AssetType.IMAGE:
                raise ImageReferenceError(f"image_reference_not_image:{asset_id}")
            if asset.mime_type.lower() not in SUPPORTED_REFERENCE_MIME_TYPES:
                raise ImageReferenceError(f"image_reference_unsupported_type:{asset_id}")
            try:
                content = open_asset(asset)
            except (AssetContentUnavailable, OSError):
                raise ImageReferenceError(f"image_reference_file_missing:{asset_id}") from None
            try:
                with content, Image.open(content) as source:
                    prepared = ImageOps.exif_transpose(source)
                    prepared.seek(0)
                    prepared = prepared.convert("RGB")
                    prepared.thumbnail((MAX_REFERENCE_EDGE, MAX_REFERENCE_EDGE), Image.Resampling.LANCZOS)
                    images.append(prepared.copy())
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                raise ImageReferenceError(f"image_reference_invalid_image:{asset_id}:{exc}") from exc
        return images
    except Exception:
        close_image_references(images)
        raise


def close_image_references(images: Iterable[Any]) -> None:
    for image in images:
        try:
            image.close()
        except Exception:
            logger.debug("suppressed error in %s", "close_image_references", exc_info=True)


def _normalize_reference_ids(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values or []:
        normalized = str(value or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _usable_reference_asset(asset: AssetRecord) -> bool:
    if asset.mime_type.lower() not in SUPPORTED_REFERENCE_MIME_TYPES:
        return False
    try:
        return asset_available(asset)
    except OSError:
        return False
