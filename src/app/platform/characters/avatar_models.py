"""Typed contracts for live-chat character avatar packs."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AvatarRenderMode = Literal["audio_envelope", "viseme", "static"]
AvatarRenderer = Literal["sprite", "live2d", "rive"]
AvatarMouthFrame = Literal["closed", "small", "medium", "wide"]


class CharacterAvatarPack(BaseModel):
    """Durable shared-image references for one Character Mode live avatar."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    character_id: str = Field(min_length=1, max_length=160)
    version: int = Field(default=1, ge=1)
    render_mode: AvatarRenderMode = "audio_envelope"
    renderer: AvatarRenderer = "sprite"
    rig_asset_id: str | None = Field(default=None, max_length=240)
    base_asset_id: str | None = Field(default=None, max_length=240)
    mouth_frames: dict[str, str] = Field(default_factory=dict)
    blink_frames: dict[str, str] = Field(default_factory=dict)
    expression_frames: dict[str, str] = Field(default_factory=dict)
    outfit_frames: dict[str, str] = Field(default_factory=dict)
    background_asset_ids: dict[str, str] = Field(default_factory=dict)
    active_outfit: str | None = Field(default=None, max_length=120)
    active_background: str | None = Field(default=None, max_length=120)
    mouth_anchor: dict[str, float] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def validate_renderable_pack(self) -> "CharacterAvatarPack":
        if self.renderer == "sprite" and not self.base_asset_id and not self.mouth_frames.get("closed"):
            raise ValueError("sprite avatar pack requires base_asset_id or a closed mouth frame")
        if self.renderer != "sprite" and not self.rig_asset_id:
            raise ValueError("rigged avatar pack requires rig_asset_id")
        for key, value in {
            **self.mouth_frames,
            **self.blink_frames,
            **self.expression_frames,
            **self.outfit_frames,
            **self.background_asset_ids,
        }.items():
            if not str(key).strip() or not str(value).strip():
                raise ValueError("avatar pack frame keys and asset IDs must be non-empty")
        return self


class UpsertCharacterAvatarPackRequest(BaseModel):
    """Browser-authored asset selections; identity ownership stays server-side."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(default=None, ge=1)
    render_mode: AvatarRenderMode = "audio_envelope"
    renderer: AvatarRenderer = "sprite"
    rig_asset_id: str | None = Field(default=None, max_length=240)
    base_asset_id: str | None = Field(default=None, max_length=240)
    mouth_frames: dict[str, str] = Field(default_factory=dict)
    blink_frames: dict[str, str] = Field(default_factory=dict)
    expression_frames: dict[str, str] = Field(default_factory=dict)
    outfit_frames: dict[str, str] = Field(default_factory=dict)
    background_asset_ids: dict[str, str] = Field(default_factory=dict)
    active_outfit: str | None = Field(default=None, max_length=120)
    active_background: str | None = Field(default=None, max_length=120)
    mouth_anchor: dict[str, float] = Field(default_factory=dict)


class DeleteCharacterAvatarPackResponse(BaseModel):
    ok: bool = True
    character_id: str


__all__ = [
    "AvatarMouthFrame",
    "AvatarRenderMode",
    "AvatarRenderer",
    "CharacterAvatarPack",
    "DeleteCharacterAvatarPackResponse",
    "UpsertCharacterAvatarPackRequest",
]
