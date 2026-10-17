"""Shared chat session contract for the web gateway."""
from __future__ import annotations

import base64
import binascii
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.conversation.contracts import (
    DEFAULT_PROFILE_ID,  # noqa: F401 - compatibility re-export
    DEFAULT_WORKSPACE_ID,  # noqa: F401 - compatibility re-export
    ChatMessage,
    ChatMessageRole,  # noqa: F401 - compatibility re-export
    ChatSession,
    ChatSessionSummary,
    InteractionMode,
    ResearchMode,
    SharedMemoryAccess,
    TranscriptPolicy,
)
from app.jobs import JobRecord

_LIVE_VOICE_TURN_ID_PATTERN = re.compile(r"voice-turn:[A-Za-z0-9_.:-]+")
_MAX_CHAT_IMAGE_DATA_URL_CHARS = 8_000_000
_MAX_CHAT_IMAGE_ATTACHMENTS = 8
_MAX_CHAT_TEXT_ATTACHMENT_CHARS = 100_000
_SUPPORTED_CHAT_IMAGE_PREFIXES = (
    "data:image/png;base64,",
    "data:image/jpeg;base64,",
    "data:image/webp;base64,",
)


def _normalize_chat_image_data_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > _MAX_CHAT_IMAGE_DATA_URL_CHARS:
        raise ValueError("image data URL is too large")
    prefix = next(
        (candidate for candidate in _SUPPORTED_CHAT_IMAGE_PREFIXES if normalized.startswith(candidate)),
        None,
    )
    if prefix is None:
        raise ValueError("image data URL must be a PNG, JPEG, or WebP data URL")
    try:
        base64.b64decode(normalized[len(prefix):], validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("image data URL must contain valid base64 data") from error
    return normalized

_EXPLICIT_QUICK_RESEARCH = re.compile(
    r"^/(?:search|quick(?:-search)?)(?:\s+)(.+)$",
    re.I | re.S,
)
_EXPLICIT_DEEP_RESEARCH = re.compile(
    r"^/(?:deep(?:-research)?)(?:\s+)(.+)$",
    re.I | re.S,
)
_EXPLICIT_RESEARCH = re.compile(
    r"^/research(?:\s+(quick|deep))?(?:\s+)(.+)$",
    re.I | re.S,
)


def parse_explicit_research_command(content: str) -> tuple[str, ResearchMode] | None:
    """Normalize explicit research slash commands into the existing research lane."""
    text = str(content or "").strip()
    match = _EXPLICIT_QUICK_RESEARCH.match(text)
    if match and match.group(1).strip():
        return match.group(1).strip(), "quick"
    match = _EXPLICIT_DEEP_RESEARCH.match(text)
    if match and match.group(1).strip():
        return match.group(1).strip(), "deep"
    match = _EXPLICIT_RESEARCH.match(text)
    if match and match.group(2).strip():
        mode: ResearchMode = "quick" if str(match.group(1) or "").casefold() == "quick" else "deep"
        return match.group(2).strip(), mode
    return None



class MessageContentPurpose(str, Enum):
    MODEL = "model"
    MEMORY = "memory"
    SUMMARY = "summary"
    SEARCH = "search"
    TRANSCRIPT = "transcript"
    AUDIT = "audit"
    EXPORT = "export"


def project_message_content(message: ChatMessage, purpose: MessageContentPurpose) -> str:
    content = message.content
    if message.role != "assistant" or message.metadata.get("delivery_status") != "interrupted":
        return content
    if purpose in {MessageContentPurpose.AUDIT, MessageContentPurpose.EXPORT}:
        return content
    metadata = _delivery_metadata(message)
    key = "visual_delivered_text_end" if purpose == MessageContentPurpose.TRANSCRIPT else "context_delivered_text_end"
    end = _bounded_content_end(metadata.get(key), len(content))
    projected = content[:end].rstrip()
    if purpose == MessageContentPurpose.TRANSCRIPT:
        return f"{projected}\n\n[Response interrupted]" if projected else "[Response interrupted]"
    return projected


def _delivery_metadata(message: ChatMessage) -> dict[str, Any]:
    metadata = dict(message.metadata)
    if "context_delivered_text_end" in metadata and "visual_delivered_text_end" in metadata:
        return metadata
    turn_id = str(metadata.get("assistant_turn_id") or "").strip()
    if not turn_id:
        return metadata
    try:
        from .assistant_turns import default_assistant_turn_coordinator

        record = default_assistant_turn_coordinator().get(turn_id)
    except Exception:
        record = None
    if record is not None:
        metadata.setdefault("visual_delivered_text_end", record.visual_delivered_text_end)
        metadata.setdefault("context_delivered_text_end", record.context_delivered_text_end)
    return metadata


def _bounded_content_end(value: Any, maximum: int) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(maximum, parsed))


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionSummary]
    next_cursor: str | None = None


class DeleteChatSessionResponse(BaseModel):
    ok: bool = True
    session_id: str


class CreateChatSessionRequest(BaseModel):
    """Client selection; trusted identity/profile content is intentionally absent."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    system_prompt: str | None = None
    research_mode_override: ResearchMode | None = None
    interaction_mode: InteractionMode = "system"
    character_id: str | None = Field(default=None, max_length=160)
    voice_asset_id: str | None = Field(default=None, max_length=240)
    read_memory: bool = False
    write_memory: bool = False
    shared_memory_access: SharedMemoryAccess = "none"
    transcript_policy: TranscriptPolicy = "persistent"

    @model_validator(mode="before")
    @classmethod
    def apply_central_defaults(cls, value: Any) -> Any:
        from app.platform.chat.session_defaults import apply_chat_session_defaults

        return apply_chat_session_defaults(value)

    @model_validator(mode="after")
    def validate_interaction_selection(self) -> "CreateChatSessionRequest":
        if self.interaction_mode == "system":
            if self.character_id:
                raise ValueError("system mode cannot select a character")
            if self.shared_memory_access != "none":
                raise ValueError("system mode cannot request shared character memory")
            return self
        if not self.character_id:
            raise ValueError("character mode requires character_id")
        if self.system_prompt:
            raise ValueError("character prompts are resolved by the server")
        from .session_identity import character_mode_available

        if not character_mode_available():
            raise ValueError("Character Mode is disabled")
        return self


class UpdateChatResearchModeRequest(BaseModel):
    research_mode_override: ResearchMode | None = None


class ChatTextAttachment(BaseModel):
    """A small, UTF-8 text file attached to a chat turn.

    Binary files are deliberately not accepted through the JSON chat API.  The
    browser reads supported text documents before sending them, which keeps the
    stored transcript and every provider prompt self-contained.
    """

    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=_MAX_CHAT_TEXT_ATTACHMENT_CHARS)

    @field_validator("filename", "mime_type")
    @classmethod
    def validate_attachment_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "\x00" in normalized or "\r" in normalized or "\n" in normalized:
            raise ValueError("attachment filename and MIME type must be single-line text")
        return normalized


class SendChatMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    image_data_url: str | None = Field(default=None, max_length=_MAX_CHAT_IMAGE_DATA_URL_CHARS)
    image_data_urls: list[str] = Field(default_factory=list, max_length=_MAX_CHAT_IMAGE_ATTACHMENTS)
    text_attachment: ChatTextAttachment | None = None
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
    research_mode: ResearchMode | None = None
    user_turn_id: str | None = Field(default=None, min_length=1, max_length=160)
    speech_segment_id: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("image_data_url")
    @classmethod
    def validate_image_data_url(cls, value: str | None) -> str | None:
        return _normalize_chat_image_data_url(value)

    @field_validator("image_data_urls")
    @classmethod
    def validate_image_data_urls(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            image = _normalize_chat_image_data_url(value)
            if image is not None:
                normalized.append(image)
        return normalized

    @model_validator(mode="after")
    def normalize_image_attachments(self) -> "SendChatMessageRequest":
        images: list[str] = []
        for value in ([self.image_data_url] if self.image_data_url else []) + list(self.image_data_urls):
            if value and value not in images:
                images.append(value)
        if len(images) > _MAX_CHAT_IMAGE_ATTACHMENTS:
            raise ValueError(f"at most {_MAX_CHAT_IMAGE_ATTACHMENTS} chat images may be attached")
        self.image_data_urls = images
        self.image_data_url = images[0] if images else None
        return self

    @model_validator(mode="before")
    @classmethod
    def normalize_explicit_research_command(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        parsed = parse_explicit_research_command(str(payload.get("content") or ""))
        if parsed is not None:
            content, mode = parsed
            payload["content"] = content
            # A narrow explicit per-turn command outranks any broader persistent
            # Agent setting or caller-supplied research preference.
            payload["research_mode"] = mode
        return payload

    @model_validator(mode="before")
    @classmethod
    def derive_live_voice_ids(cls, value: Any) -> Any:
        """Honor the browser's existing live turn marker without expanding the API."""

        if not isinstance(value, dict):
            return value
        payload = dict(value)
        live_turn_id = str(payload.get("live_voice_turn_id") or "").strip()
        if (
            not live_turn_id
            or len(live_turn_id) > 120
            or _LIVE_VOICE_TURN_ID_PATTERN.fullmatch(live_turn_id) is None
        ):
            return payload
        payload.setdefault("user_turn_id", f"voice-user-turn:{live_turn_id}")
        payload.setdefault("speech_segment_id", f"voice-segment:{live_turn_id}")
        return payload


class SendChatMessageResponse(BaseModel):
    session: ChatSession
    user_message: ChatMessage
    job: JobRecord
    generation_status: Literal["queued"] = "queued"
