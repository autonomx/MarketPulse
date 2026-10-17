"""Portable reference-image transport for the standalone image service."""
from __future__ import annotations

import base64
import binascii
import io
from collections.abc import Iterable
from typing import Any

from .reference_assets import (
    MAX_REFERENCE_COUNT,
    MAX_REFERENCE_EDGE,
    MAX_REFERENCE_UPLOAD_BYTES,
    ImageReferenceError,
    close_image_references,
    load_image_reference_assets,
)

REFERENCE_IMAGES_PAYLOAD_KEY = "reference_images_base64"


def encode_reference_assets(asset_ids: Iterable[str]) -> list[str]:
    """Resolve backend asset IDs and encode normalized PNGs for HTTP transport."""

    images = load_image_reference_assets(asset_ids)
    try:
        encoded: list[str] = []
        for image in images:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
            encoded.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
        return encoded
    finally:
        close_image_references(images)


def decode_reference_payloads(values: Iterable[str]) -> list[Any]:
    """Decode reference images supplied by another process without manifest access."""

    payloads = [str(value or "").strip() for value in values or [] if str(value or "").strip()]
    if not payloads:
        return []
    if len(payloads) > MAX_REFERENCE_COUNT:
        raise ImageReferenceError(
            f"image_reference_limit_exceeded:count={len(payloads)} max={MAX_REFERENCE_COUNT}"
        )

    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise ImageReferenceError("image_reference_runtime_missing:Pillow") from exc

    images: list[Any] = []
    try:
        for index, payload in enumerate(payloads):
            if payload.startswith("data:"):
                _, separator, payload = payload.partition(",")
                if not separator:
                    raise ImageReferenceError(f"image_reference_invalid_transport:{index}")
            if len(payload) > ((MAX_REFERENCE_UPLOAD_BYTES * 4) // 3) + 8:
                raise ImageReferenceError(
                    f"image_reference_transport_too_large:index={index} max_bytes={MAX_REFERENCE_UPLOAD_BYTES}"
                )
            try:
                data = base64.b64decode(payload, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ImageReferenceError(f"image_reference_invalid_transport:{index}") from exc
            if not data:
                raise ImageReferenceError(f"image_reference_empty_transport:{index}")
            if len(data) > MAX_REFERENCE_UPLOAD_BYTES:
                raise ImageReferenceError(
                    f"image_reference_transport_too_large:index={index} size_bytes={len(data)} "
                    f"max_bytes={MAX_REFERENCE_UPLOAD_BYTES}"
                )
            try:
                with Image.open(io.BytesIO(data)) as source:
                    source.verify()
                with Image.open(io.BytesIO(data)) as source:
                    prepared = ImageOps.exif_transpose(source)
                    prepared.seek(0)
                    prepared = prepared.convert("RGB")
                    prepared.thumbnail((MAX_REFERENCE_EDGE, MAX_REFERENCE_EDGE), Image.Resampling.LANCZOS)
                    images.append(prepared.copy())
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                raise ImageReferenceError(f"image_reference_invalid_transport:{index}:{exc}") from exc
        return images
    except Exception:
        close_image_references(images)
        raise
