"""Image feature contract (ADR-0016): job payloads and the ports other modules implement."""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, Field, field_validator

from app.runtime.ports import Port


ImageJobErrorCode = Literal[
    "image_generation_disabled",
    "image_provider_unavailable",
    "image_invalid_request",
    "image_generation_failed",
    "image_output_missing",
    "image_asset_store_failed",
]


class ImageGenerateInput(BaseModel):
    """Validated payload accepted by the shared ``image.generate`` executor."""

    prompt: str = Field(min_length=1, max_length=8_000)
    negative_prompt: str = Field(default="", max_length=8_000)
    provider_id: str = ""
    width: int = Field(default=768, ge=128, le=4_096)
    height: int = Field(default=768, ge=128, le=4_096)
    style: str = Field(default="", max_length=200)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=2)
    seed: int | None = Field(default=None, ge=0)
    steps: int | None = Field(default=None, ge=1, le=200)
    guidance_scale: float | None = Field(default=None, ge=0, le=100)
    unload_after_generation: bool = False
    no_cache: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("prompt", "negative_prompt", "provider_id", "style", mode="before")
    @classmethod
    def normalize_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("reference_asset_ids", mode="before")
    @classmethod
    def normalize_reference_asset_ids(cls, value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        result: list[str] = []
        for item in value:
            normalized = str(item or "").strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    @field_validator("width", "height")
    @classmethod
    def validate_dimension_step(cls, value: int) -> int:
        if value % 64:
            raise ValueError("image dimensions must be multiples of 64")
        return value

    def provider_key(self) -> str:
        return normalize_image_provider_id(self.provider_id)

    def provider_payload(self) -> dict[str, Any]:
        """Map the shared contract to the existing image service payload."""

        return {
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "provider": self.provider_key(),
            "width": self.width,
            "height": self.height,
            "style": self.style,
            "reference_asset_ids": list(self.reference_asset_ids),
            "seed": self.seed,
            "steps": self.steps,
            "guidance_scale": self.guidance_scale,
            "unload_after_generation": self.unload_after_generation,
            "no_cache": self.no_cache or bool(self.reference_asset_ids),
            "metadata": dict(self.metadata),
        }


class ImageOutputRef(BaseModel):
    """Small metadata-only reference emitted by a completed image job."""

    type: Literal["image"] = "image"
    asset_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    provider_id: str = ""
    seed: int | None = None


def normalize_image_provider_id(provider_id: str | None) -> str:
    """Convert a facade image provider ID into the image-service registry key."""

    normalized = str(provider_id or "").strip()
    if not normalized:
        return ""
    if ":" not in normalized:
        return normalized
    family, key = normalized.split(":", 1)
    if family != "image":
        raise ValueError(f"provider is not a standalone image provider: {normalized}")
    if not key.strip():
        raise ValueError("image provider key is empty")
    return key.strip()


def image_title_from_prompt(prompt: str, *, fallback: str = "Generated image", limit: int = 80) -> str:
    compact = " ".join(str(prompt or "").split())
    if not compact:
        return fallback
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 1)].rstrip() + "…"


class CharacterAvatarFinisher(Protocol):
    """Character-specific steps of an image job that generates an avatar frame."""

    def stabilize(
        self, job: Any, request: Any, storage_path: str, request_metadata: dict[str, Any], store: Any,
    ) -> dict[str, Any]:
        """Adjust a generated frame and return metadata to record with the asset."""
        ...

    def completed(self, job: Any) -> None:
        """React to a finished avatar image job."""
        ...


CHARACTER_AVATAR_FINISHER: Port[CharacterAvatarFinisher] = Port(
    "image.character_avatar_finisher", CharacterAvatarFinisher, "at_most_one",
)


# Image services other modules use, loaded on first use (ADR-0016): importing
# the contract does not load providers or reference-asset code.
_ENTRY_POINTS = {
    "BaseImageProvider": "providers.base",
    "ImageGenerationResult": "providers.base",
    "FluxKleinImageProvider": "providers.flux_klein_provider",
    "ImageReferenceError": "reference_assets",
    "close_image_references": "reference_assets",
    "load_image_reference_assets": "reference_assets",
}

if TYPE_CHECKING:
    from .providers.base import BaseImageProvider as BaseImageProvider, ImageGenerationResult as ImageGenerationResult
    from .providers.flux_klein_provider import FluxKleinImageProvider as FluxKleinImageProvider
    from .reference_assets import (
        ImageReferenceError as ImageReferenceError,
        close_image_references as close_image_references,
        load_image_reference_assets as load_image_reference_assets,
    )


def __getattr__(name: str) -> Any:
    module = _ENTRY_POINTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__package__}.{module}"), name)
