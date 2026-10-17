from __future__ import annotations

from typing import Dict, Optional

from .base import BaseImageProvider, ImageGenerationResult


class DisabledImageProvider(BaseImageProvider):
    """Null provider used when image generation is disabled."""

    provider_name = "disabled"

    def __init__(self, config: Dict[str, object] | None = None):
        self.config = dict(config or {})

    def is_available(self) -> bool:
        return True

    def runtime_status(self) -> Dict[str, object]:
        return {
            "provider": self.provider_name,
            "ready": True,
            "status": "disabled",
            "summary": "VISUALS DISABLED",
            "error": "",
            "details": {
                "enabled": False,
            },
        }

    def unload(self) -> None:
        return None

    def generate(  # type: ignore[override]  # RPG's keyword image request
        self,
        *,
        prompt: str,
        seed: Optional[int],
        style: str,
        model: str,
        kind: str,
        target_id: str,
    ) -> ImageGenerationResult:
        return ImageGenerationResult(
            ok=False,
            status="failed",
            error="visual_provider_disabled",
            moderation_status="approved",
            moderation_reason="",
        )