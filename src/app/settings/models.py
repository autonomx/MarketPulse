"""Settings API DTOs owned by the settings kernel."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SettingsPayload(BaseModel):
    success: bool = True
    provider: str
    audio_provider_tts: str
    audio_provider_stt: str
    image_enabled: bool
    rpg_visual_enabled: bool
    worker_urls: dict[str, str] = Field(default_factory=dict)
    hermes_status: dict[str, Any] = Field(default_factory=dict)
    hermes_commands: dict[str, str] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    # Each setting's revision (0 = never saved); send them back in a save so a
    # concurrent change is refused instead of overwritten.
    revisions: dict[str, int] = Field(default_factory=dict)


class SettingsSaveResponse(BaseModel):
    success: bool = True


class SettingsProfileSaveRequest(BaseModel):
    """A Settings Control Center save: a profile patch plus the provider sections it changed.

    ``base_revision`` is the profile revision the patch was made against; a
    newer profile refuses the save. Provider sections are merged into the
    stored settings, and an ``api_key`` in them goes to the secret store
    (a masked ``***`` value leaves the stored key unchanged).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    base_revision: str | None = None
    settings_profile_patch: dict[str, Any] | None = None
    provider: str | None = None
    audio_provider_tts: str | None = None
    audio_provider_stt: str | None = None
    lmstudio: dict[str, Any] | None = None
    openrouter: dict[str, Any] | None = None
    cerebras: dict[str, Any] | None = None
    llamacpp: dict[str, Any] | None = None
    faster_qwen3_tts: dict[str, Any] | None = Field(default=None, alias="faster-qwen3-tts")
    parakeet: dict[str, Any] | None = None
    image: dict[str, Any] | None = None
