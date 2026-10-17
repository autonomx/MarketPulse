"""Typed Character Mode contracts shared by Chat, voice, and memory."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.contracts import InteractionMode, SharedMemoryAccess, TranscriptPolicy  # noqa: F401 - re-exported
# The interaction types are the kernel's (PA-1.3); characters re-exports them.
from app.conversation.contracts import (  # noqa: F401
    SYSTEM_ASSISTANT_ID,
    SYSTEM_ASSISTANT_IDENTITY,
    SYSTEM_ASSISTANT_NAME,
    InteractionSelection,
    ResolvedInteractionContext,
)

CharacterStatus = Literal["active", "archived"]
DEFAULT_CHARACTER_IDENTITY_POLICY: dict[str, Any] = {
    "may_claim_to_be_human": False,
    "may_claim_real_world_experiences": False,
    "disclosure_required": True,
}
DEFAULT_CHARACTER_SHARED_MEMORY_POLICY: dict[str, Any] = {
    "access": "none",
    "allowed_categories": [],
}


class CharacterProfileSnapshot(BaseModel):
    """Immutable server-owned character identity used during prompt resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=160)
    personality_prompt: str = Field(min_length=1)
    default_greeting: str = Field(default="", max_length=2_000)
    default_voice_asset_id: str | None = Field(default=None, max_length=240)
    speech_style: dict[str, Any] = Field(default_factory=dict)
    identity_policy: dict[str, Any] = Field(default_factory=dict)
    shared_memory_policy: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    enabled: bool = True


class CharacterProfile(BaseModel):
    """Current durable character profile."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4_000)
    personality_prompt: str = Field(min_length=1)
    default_greeting: str = Field(default="", max_length=2_000)
    default_voice_asset_id: str | None = Field(default=None, max_length=240)
    speech_style: dict[str, Any] = Field(default_factory=dict)
    identity_policy: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_CHARACTER_IDENTITY_POLICY))
    shared_memory_policy: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_CHARACTER_SHARED_MEMORY_POLICY))
    active_version: int = Field(default=1, ge=1)
    enabled: bool = True
    status: CharacterStatus = "active"
    created_at: str
    updated_at: str

    def snapshot(self) -> CharacterProfileSnapshot:
        return CharacterProfileSnapshot(
            id=self.id,
            display_name=self.display_name,
            personality_prompt=self.personality_prompt,
            default_greeting=self.default_greeting,
            default_voice_asset_id=self.default_voice_asset_id,
            speech_style=dict(self.speech_style),
            identity_policy=dict(self.identity_policy),
            shared_memory_policy=dict(self.shared_memory_policy),
            version=self.active_version,
            enabled=self.enabled and self.status == "active",
        )


class CharacterProfileVersion(BaseModel):
    """Immutable historical character profile version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    character_id: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4_000)
    personality_prompt: str = Field(min_length=1)
    default_greeting: str = Field(default="", max_length=2_000)
    default_voice_asset_id: str | None = Field(default=None, max_length=240)
    speech_style: dict[str, Any] = Field(default_factory=dict)
    identity_policy: dict[str, Any] = Field(default_factory=dict)
    shared_memory_policy: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class CharacterListResponse(BaseModel):
    characters: list[CharacterProfile] = Field(default_factory=list)


class CharacterVersionListResponse(BaseModel):
    versions: list[CharacterProfileVersion] = Field(default_factory=list)


class CreateCharacterRequest(BaseModel):
    """User-authored profile content; the server owns identity and persistence fields."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=160)
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4_000)
    personality_prompt: str = Field(min_length=1)
    default_greeting: str = Field(default="", max_length=2_000)
    default_voice_asset_id: str | None = Field(default=None, max_length=240)
    speech_style: dict[str, Any] = Field(default_factory=dict)
    identity_policy: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_CHARACTER_IDENTITY_POLICY))
    shared_memory_policy: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_CHARACTER_SHARED_MEMORY_POLICY))
    enabled: bool = True


class UpdateCharacterRequest(BaseModel):
    """Partial profile update with optimistic version protection."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4_000)
    personality_prompt: str | None = Field(default=None, min_length=1)
    default_greeting: str | None = Field(default=None, max_length=2_000)
    default_voice_asset_id: str | None = Field(default=None, max_length=240)
    clear_default_voice: bool = False
    speech_style: dict[str, Any] | None = None
    identity_policy: dict[str, Any] | None = None
    shared_memory_policy: dict[str, Any] | None = None
    enabled: bool | None = None


class ArchiveCharacterResponse(BaseModel):
    ok: bool = True
    character: CharacterProfile
