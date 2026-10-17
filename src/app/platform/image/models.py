"""Shared request/response models for global image generation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ImageGenerationRequest:
    provider: str = "flux_klein"
    prompt: str = ""
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    seed: Optional[int] = None
    steps: Optional[int] = None
    guidance_scale: Optional[float] = None
    kind: str = "image"          # portrait | scene | item | cover | image
    source: str = "app"          # rpg | chat | story | app | ...
    style: str = ""
    reference_asset_ids: list[str] = field(default_factory=list)
    session_id: str = ""
    request_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageGenerationResponse:
    ok: bool
    provider: str
    status: str
    error: str = ""
    asset_url: str = ""
    local_path: str = ""
    # Shared blob-store copy, for gateways that cannot read the image
    # service's local disk (multi-host deployments with S3 storage).
    blob_key: str = ""
    checksum_sha256: str = ""
    seed: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    mime_type: str = "image/png"
    # Compatibility / caller-specific extras such as revised_prompt,
    # provider dimensions, source context, etc.
    metadata: Dict[str, Any] = field(default_factory=dict)
