"""Contracts for web research and desktop context enrichment."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.conversation.contracts import AssistantContextItem
from app.providers.desktop_vision import DesktopCaptureMode
from app.conversation.contracts import ResearchMode, normalize_research_mode
from app.conversation.research_compatibility import (
    LEGACY_RESEARCH_FIELDS,
    LEGACY_RESEARCH_MODES,
    legacy_research_aliases_enabled,
    legacy_research_warnings,
    record_legacy_research_aliases,
)

LiveConversationRepairKind = Literal[
    "acknowledge_correction",
    "clarify_number",
    "clarify_name",
    "yield_to_user",
    "resume_interrupted_thought",
]


class LiveConversationRepairContext(BaseModel):
    kind: LiveConversationRepairKind
    instruction: str = Field(min_length=1, max_length=280)
    source_reason: str = Field(min_length=1, max_length=120)
    confidence: float = Field(default=1.0, ge=0, le=1)


class AssistantContextChatRequest(BaseModel):
    content: str = Field(min_length=1)
    user_turn_id: str | None = Field(default=None, min_length=1, max_length=160)
    image_data_url: str | None = None
    image_data_urls: list[str] = Field(default_factory=list, max_length=8)
    text_attachment: dict[str, Any] | None = None
    provider_id: str | None = None
    model_id: str | None = None
    agent_mode: bool = False
    coding_approval_policy: Literal[
        "always_ask",
        "ask_sensitive",
        "allow_automatic",
    ] = "ask_sensitive"
    dry_run: bool = False
    workspace_root: str | None = Field(default=None, max_length=4096)
    web_research_mode: ResearchMode = "disabled"
    allow_research_downgrade: bool = False
    internal_research_identity: str | None = Field(default=None, exclude=True)
    internal_research_provider: str | None = Field(default=None, exclude=True)
    internal_research_provider_chain: list[str] = Field(default_factory=list, exclude=True)
    internal_research_policy: dict[str, Any] = Field(default_factory=dict, exclude=True)
    internal_research_warnings: list[str] = Field(default_factory=list, exclude=True)
    web_search_max_results: int = Field(default=5, ge=1, le=8)
    deep_research_max_pages: int | None = Field(default=None, ge=1, le=100)
    desktop_image_data_url: str | None = None
    desktop_current_image_data_url: str | None = None
    desktop_history_image_data_url: str | None = None
    desktop_combined_image_data_url: str | None = None
    desktop_history_timestamps: list[float] = Field(default_factory=list, max_length=8)
    desktop_capture_mode: DesktopCaptureMode = "single"
    desktop_question: str | None = None
    vision_model_id: str | None = None
    live_repair: LiveConversationRepairContext | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_temporary_server_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        payload.pop("internal_research_identity", None)
        payload.pop("internal_research_provider", None)
        payload.pop("internal_research_provider_chain", None)
        payload.pop("internal_research_policy", None)
        payload.pop("internal_research_warnings", None)

        aliases = [field for field in LEGACY_RESEARCH_FIELDS if field in payload]
        selected = payload.get("web_research_mode")
        if selected is None and "web_search_mode" in payload:
            selected = payload.get("web_search_mode")
        normalized_selected = str(selected or "").strip().lower().replace("-", "_")
        if normalized_selected in LEGACY_RESEARCH_MODES:
            aliases.append(f"mode:{normalized_selected}")

        if aliases and not legacy_research_aliases_enabled():
            raise ValueError("legacy_research_aliases_disabled")
        if aliases:
            record_legacy_research_aliases(aliases)
            payload["internal_research_warnings"] = legacy_research_warnings(aliases)

        legacy_modes = {
            "automatic": "quick",
            "manual": "quick",
            "quick_search": "quick",
            "deep_research": "deep",
        }
        payload["web_research_mode"] = legacy_modes.get(
            normalized_selected,
            normalize_research_mode(selected),
        )
        for field in LEGACY_RESEARCH_FIELDS:
            payload.pop(field, None)
        return payload

    @model_validator(mode="after")
    def validate_chat_attachment(self) -> "AssistantContextChatRequest":
        """Apply the canonical chat attachment contract before enrichment.

        Context-enabled requests use a separate endpoint, so validation must
        occur here as request validation rather than later in the route after
        research or desktop context work has started.
        """

        from app.platform.chat.models import SendChatMessageRequest

        validated = SendChatMessageRequest.model_validate({
            "content": self.content,
            "user_turn_id": self.user_turn_id,
            "image_data_url": self.image_data_url,
            "image_data_urls": self.image_data_urls,
            "text_attachment": self.text_attachment,
        })
        self.image_data_url = validated.image_data_url
        self.image_data_urls = list(validated.image_data_urls)
        self.user_turn_id = validated.user_turn_id
        self.text_attachment = (
            validated.text_attachment.model_dump()
            if validated.text_attachment is not None
            else None
        )
        return self


class AssistantContextBuildResult(BaseModel):
    items: list[AssistantContextItem] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
