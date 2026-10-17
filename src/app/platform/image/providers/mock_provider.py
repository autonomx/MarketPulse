"""Mock image provider for testing and fallback."""
from __future__ import annotations

import io
import os
from typing import Any, Dict

from PIL import Image, ImageDraw

from app.platform.image.providers.base import BaseImageProvider, ImageGenerationResult
from app.runtime.paths import generated_images_root


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


class MockImageProvider(BaseImageProvider):
    provider_name = "mock"

    def is_loaded(self) -> bool:
        return True

    def generate(self, payload: Dict[str, Any]) -> ImageGenerationResult:
        width = _safe_int(payload.get("width"), 1024)
        height = _safe_int(payload.get("height"), 1024)
        prompt = _safe_str(payload.get("prompt")).strip()
        kind = _safe_str(payload.get("kind")).strip() or "image"

        image = Image.new("RGB", (width, height), color=(32, 32, 40))
        draw = ImageDraw.Draw(image)
        text = f"MOCK IMAGE\nkind={kind}\n{prompt[:120]}"
        draw.text((20, 20), text, fill=(220, 220, 220))

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()

        out_dir = str(generated_images_root())
        os.makedirs(out_dir, exist_ok=True)
        file_path = os.path.join(out_dir, f"mock_{kind}.png")
        with open(file_path, "wb") as f:
            f.write(image_bytes)

        return ImageGenerationResult(
            ok=True,
            status="completed",
            error="",
            moderation_status="approved",
            moderation_reason="",
            image_bytes=image_bytes,
            mime_type="image/png",
            revised_prompt=prompt,
            file_path=file_path,
            asset_url="",
            metadata={"provider": "mock", "width": width, "height": height},
        )
