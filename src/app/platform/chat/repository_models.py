from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChatImportState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    source_hash: str
    status: str
    imported_session_count: int = Field(ge=0)
    imported_message_count: int = Field(ge=0)
    skipped_session_count: int = Field(ge=0)
    errors: list[str] = Field(default_factory=list)
    updated_at: str
