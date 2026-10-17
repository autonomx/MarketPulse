"""Contracts for Character Mode avatar generation and cloned-voice backfill."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AvatarGenerationStatus = Literal[
    "queued",
    "generating_base",
    "generating_variants",
    "completed",
    "failed",
]


class CreateCharacterAvatarGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appearance_prompt: str = Field(default="", max_length=4_000)
    style: str = Field(default="illustrated character portrait", max_length=200)
    outfit_prompt: str = Field(default="", max_length=1_000)
    background_prompt: str = Field(default="", max_length=1_000)
    provider_id: str = Field(default="image:flux_klein", max_length=200)
    width: int = Field(default=768, ge=256, le=2_048, multiple_of=64)
    height: int = Field(default=768, ge=256, le=2_048, multiple_of=64)
    seed: int | None = Field(default=None, ge=0)
    steps: int = Field(default=4, ge=1, le=80)
    guidance_scale: float | None = Field(default=None, ge=0, le=30)
    include_blink: bool = True
    include_expressions: bool = True
    include_outfit: bool = True
    include_background: bool = True
    unload_after_generation: bool = False
    source_asset_id: str = Field(default="", max_length=300)
    source_image_consent_confirmed: bool = False

    @field_validator("provider_id", mode="before")
    @classmethod
    def normalize_avatar_provider(cls, value: Any) -> str:
        provider = str(value or "").strip().lower()
        if not provider or provider == "flux_klein":
            return "image:flux_klein"
        if provider != "image:flux_klein":
            raise ValueError(
                "character_avatar_requires_flux_klein: avatar mouth, blink, and expression "
                "frames require an image-to-image provider"
            )
        return provider


class CharacterAvatarGenerationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    character_id: str
    status: AvatarGenerationStatus
    request: CreateCharacterAvatarGenerationRequest
    base_job_id: str
    variant_job_ids: dict[str, str] = Field(default_factory=dict)
    asset_ids: dict[str, str] = Field(default_factory=dict)
    avatar_pack_version: int | None = Field(default=None, ge=1)
    error: str = ""
    created_at: str
    updated_at: str


class CharacterAvatarGenerationListResponse(BaseModel):
    batches: list[CharacterAvatarGenerationBatch] = Field(default_factory=list)


class BackfillClonedVoiceCharactersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue_avatar_generation: bool = True
    appearance_template: str = Field(
        default=(
            "Create an original fictional companion whose visual design matches the tone "
            "suggested by the voice name. Do not depict or imitate a real public person."
        ),
        max_length=4_000,
    )
    style: str = Field(default="illustrated character portrait", max_length=200)
    provider_id: str = Field(default="", max_length=200)
    include_reference_profiles: bool = False


class ClonedVoiceCharacterBackfillItem(BaseModel):
    voice_asset_id: str
    display_name: str
    character_id: str | None = None
    result: Literal[
        "created",
        "existing",
        "queued",
        "already_has_avatar",
        "skipped",
        "failed",
    ]
    generation_batch_id: str | None = None
    reason: str = ""


class BackfillClonedVoiceCharactersResponse(BaseModel):
    items: list[ClonedVoiceCharacterBackfillItem] = Field(default_factory=list)

    @property
    def created_count(self) -> int:
        return sum(item.result == "created" for item in self.items)

    @property
    def queued_count(self) -> int:
        return sum(item.generation_batch_id is not None for item in self.items)


__all__ = [
    "AvatarGenerationStatus",
    "BackfillClonedVoiceCharactersRequest",
    "BackfillClonedVoiceCharactersResponse",
    "CharacterAvatarGenerationBatch",
    "CharacterAvatarGenerationListResponse",
    "ClonedVoiceCharacterBackfillItem",
    "CreateCharacterAvatarGenerationRequest",
]
