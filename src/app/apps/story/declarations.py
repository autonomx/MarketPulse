"""What the kernel reads about the storyteller and podcast module without loading it (ADR-0016, PA-2.1)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.persistence.declarations import SettingsSection


class StorytellerSettingsProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    provider_id: str = Field("", alias="providerId")
    model_id: str = Field("", alias="modelId")
    tone: str = "Cozy"
    writing_style: str = Field("Lyrical & Descriptive", alias="writingStyle")
    read_speed: float = Field(1.0, alias="readSpeed")
    pause_paragraph_ms: int = Field(500, alias="pauseParagraphMs")
    pause_chapter_ms: int = Field(1200, alias="pauseChapterMs")
    read_chapter_titles: bool = Field(True, alias="readChapterTitles")
    read_style_preset: str = Field("Dramatic audiobook", alias="readStylePreset")
    pronunciation: dict[str, str] = Field(default_factory=dict)


class PodcastSettingsProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    provider_id: str = Field("", alias="providerId")
    model_id: str = Field("", alias="modelId")
    format: str = "interview"
    duration_minutes: int = Field(5, alias="durationMinutes")
    tone: str = "Professional"
    language: str = "English (US)"
    generation_style: str = Field("automatic", alias="generationStyle")
    autoplay: bool = False
    playback_rate: float = Field(1.0, alias="playbackRate")
    stability: float = 0.72
    similarity: float = 0.78
    effects: list[str] = Field(default_factory=list)


SETTINGS = (
    SettingsSection("storyteller", StorytellerSettingsProfile, order=20),
    SettingsSection("podcast", PodcastSettingsProfile, order=30),
)
