"""Base classes for global image providers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ImageGenerationResult:
    ok: bool
    status: str
    error: str = ""
    moderation_status: str = "approved"
    moderation_reason: str = ""
    image_bytes: bytes | None = None
    mime_type: str = "image/png"
    revised_prompt: str = ""
    file_path: str = ""
    asset_url: str = ""
    metadata: Optional[Dict[str, Any]] = None


class BaseImageProvider:
    def __init__(self, config: Dict[str, Any] | None = None):
        self.config = dict(config or {})

    def load(self):
        return None

    def unload(self):
        return None

    def is_loaded(self) -> bool:
        """Return whether heavyweight provider resources are resident."""

        return False

    def generate(self, payload: Dict[str, Any]) -> ImageGenerationResult:
        raise NotImplementedError
