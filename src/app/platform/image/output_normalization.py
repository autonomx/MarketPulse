"""Normalize generated PIL images before storing browser-facing assets."""
from __future__ import annotations

import math
from typing import Any

from PIL import Image, ImageOps

DEFAULT_MAX_BROWSER_EDGE = 8192
DEFAULT_MAX_BROWSER_PIXELS = 32 * 1024 * 1024


def normalize_generated_image(
    image: Image.Image,
    *,
    max_edge: int = DEFAULT_MAX_BROWSER_EDGE,
    max_pixels: int = DEFAULT_MAX_BROWSER_PIXELS,
) -> tuple[Image.Image, dict[str, Any]]:
    """Return an opaque RGB image with browser-safe dimensions.

    Diffusers pipelines do not all return identical PIL modes. In particular,
    an image carrying an unexpected transparent alpha channel can load
    successfully in the browser while appearing as an empty dark preview.
    Normalizing at the asset boundary keeps every provider's output portable.
    """

    if not isinstance(image, Image.Image):
        raise TypeError(f"unsupported_generated_image:{type(image).__name__}")

    original_mode = str(image.mode or "")
    original_size = tuple(int(value) for value in image.size)
    if len(original_size) != 2 or original_size[0] <= 0 or original_size[1] <= 0:
        raise ValueError(f"invalid_generated_image_size:{original_size}")

    normalized = ImageOps.exif_transpose(image)
    had_alpha = "A" in normalized.getbands() or "transparency" in normalized.info
    if normalized.mode != "RGB":
        # Generated image assets are intentionally opaque. Dropping an
        # unexpected alpha channel preserves the model's RGB values instead of
        # allowing a fully transparent result to disappear on a dark canvas.
        normalized = normalized.convert("RGB")
    else:
        normalized = normalized.copy()

    width, height = normalized.size
    edge_scale = min(1.0, max_edge / float(max(width, height))) if max_edge > 0 else 1.0
    pixel_scale = (
        min(1.0, math.sqrt(max_pixels / float(width * height)))
        if max_pixels > 0 and width * height > 0
        else 1.0
    )
    scale = min(edge_scale, pixel_scale)
    resized = scale < 1.0
    if resized:
        target = (
            max(1, int(round(width * scale))),
            max(1, int(round(height * scale))),
        )
        normalized = normalized.resize(target, Image.Resampling.LANCZOS)

    return normalized, {
        "source_mode": original_mode,
        "source_width": original_size[0],
        "source_height": original_size[1],
        "normalized_mode": normalized.mode,
        "normalized_width": normalized.width,
        "normalized_height": normalized.height,
        "alpha_removed": had_alpha,
        "resized_for_browser": resized,
    }
