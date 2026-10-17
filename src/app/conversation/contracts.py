"""Data contracts shared by chat, research, memory and character features."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


class AssistantContextItem(BaseModel):
    source_id: Literal["web_search", "desktop_vision", "live_repair"]
    title: str
    content: str
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


InteractionMode = Literal["system", "character"]
SharedMemoryAccess = Literal["none", "read_only"]
TranscriptPolicy = Literal["persistent", "temporary", "none"]
ResearchMode = Literal["disabled", "quick", "deep"]


# Who a chat session speaks as (PA-1.3): the selection, its resolved identity and the system
# assistant. Chat resolves the system assistant itself; characters resolves a character
# through chat's CHARACTER_RESOLVER port.
SYSTEM_ASSISTANT_ID = "system-assistant"
SYSTEM_ASSISTANT_NAME = "System Assistant"
SYSTEM_ASSISTANT_IDENTITY = (
    "You are the user's configurable System Assistant. Follow the selected assistant "
    "style while remaining clear, accurate, practical, and honest about uncertainty."
)


class CharacterInteractionError(ValueError):
    """Base error for rejected character interaction selections."""


class CharacterModeDisabledError(CharacterInteractionError):
    pass


class CharacterResolutionError(CharacterInteractionError):
    pass


class InteractionSelection(BaseModel):
    """Untrusted client selection before server-side profile resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    interaction_mode: InteractionMode = "system"
    character_id: str | None = Field(default=None, max_length=160)
    voice_asset_id: str | None = Field(default=None, max_length=240)
    read_memory: bool = False
    write_memory: bool = False
    shared_memory_access: SharedMemoryAccess = "none"
    transcript_policy: TranscriptPolicy = "persistent"


class ResolvedInteractionContext(BaseModel):
    """Trusted effective interaction identity produced only by the backend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    interaction_mode: InteractionMode
    owner_type: Literal["system", "character"]
    owner_id: str
    display_name: str
    character_id: str | None = None
    voice_asset_id: str | None = None
    read_memory: bool = False
    write_memory: bool = False
    shared_memory_access: SharedMemoryAccess = "none"
    transcript_policy: TranscriptPolicy = "persistent"
    character_profile_version: int | None = Field(default=None, ge=1)
    assistant_identity: list[str] = Field(default_factory=list)
    effective_identity_hash: str = Field(min_length=64, max_length=64)


class SetSessionInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interaction_mode: InteractionMode
    character_id: str | None = Field(default=None, max_length=160)
    voice_asset_id: str | None = Field(default=None, max_length=240)
    read_memory: bool = False
    write_memory: bool = False
    shared_memory_access: SharedMemoryAccess = "none"
    transcript_policy: TranscriptPolicy = "persistent"
    continue_topic: bool = False

    @model_validator(mode="after")
    def validate_selection(self) -> "SetSessionInteractionRequest":
        if self.interaction_mode == "character" and not self.character_id:
            raise ValueError("character mode requires character_id")
        if self.interaction_mode == "system" and self.character_id:
            raise ValueError("system mode cannot select character_id")
        if self.interaction_mode == "system" and (
            self.read_memory or self.write_memory or self.shared_memory_access != "none"
        ):
            raise ValueError("system memory is controlled by normal Chat memory settings")
        return self


def interaction_identity_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def resolve_system_interaction(selection: InteractionSelection) -> ResolvedInteractionContext:
    """The system assistant's identity for a system-mode selection."""
    if selection.interaction_mode != "system":
        raise CharacterResolutionError("not a system-mode selection")
    if selection.character_id:
        raise CharacterResolutionError("system mode cannot select a character")
    payload: dict[str, Any] = {
        "interaction_mode": "system",
        "owner_type": "system",
        "owner_id": SYSTEM_ASSISTANT_ID,
        "display_name": SYSTEM_ASSISTANT_NAME,
        "voice_asset_id": selection.voice_asset_id,
        "read_memory": selection.read_memory,
        "write_memory": selection.write_memory,
        "shared_memory_access": "none",
        "transcript_policy": selection.transcript_policy,
        "assistant_identity": [SYSTEM_ASSISTANT_IDENTITY],
    }
    return ResolvedInteractionContext(**payload, effective_identity_hash=interaction_identity_hash(payload))


def session_interaction_selection(session: object) -> InteractionSelection:
    """The selection a persisted session holds."""
    return InteractionSelection(
        interaction_mode=getattr(session, "interaction_mode", "system"),
        character_id=getattr(session, "character_id", None),
        voice_asset_id=getattr(session, "voice_asset_id", None),
        read_memory=bool(getattr(session, "read_memory", False)),
        write_memory=bool(getattr(session, "write_memory", False)),
        shared_memory_access=getattr(session, "shared_memory_access", "none"),
        transcript_policy=getattr(session, "transcript_policy", "persistent"),
    )


def normalize_research_mode(value: Any) -> ResearchMode:
    """Normalize only canonical values; legacy aliases use the compatibility adapter."""

    normalized = str(value or "").strip().lower()
    if normalized in {"disabled", "quick", "deep"}:
        return normalized  # type: ignore[return-value]
    return "disabled"
ChatMessageRole = Literal["system", "user", "assistant"]
DEFAULT_PROFILE_ID = "profile:local"
DEFAULT_WORKSPACE_ID = "workspace:default"
LIVE_VOICE_ROUTE_METADATA_KEY = "omnix_provider_route"


class ChatMessage(BaseModel):
    id: str
    role: ChatMessageRole
    content: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatSessionSummary(BaseModel):
    id: str
    title: str
    provider_id: str | None = None
    model_id: str | None = None
    research_mode_override: ResearchMode | None = None
    profile_id: str = DEFAULT_PROFILE_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str | None = None
    memory_enabled: bool = False
    memory_snapshot_id: str | None = None
    memory_snapshot_revision: int | None = Field(default=None, ge=1)
    memory_record_count: int = Field(default=0, ge=0)
    memory_last_refreshed_at: str | None = None
    interaction_mode: InteractionMode = "system"
    character_id: str | None = Field(default=None, max_length=160)
    voice_asset_id: str | None = Field(default=None, max_length=240)
    read_memory: bool = False
    write_memory: bool = False
    shared_memory_access: SharedMemoryAccess = "none"
    transcript_policy: TranscriptPolicy = "persistent"
    active_segment_id: str | None = Field(default=None, max_length=200)
    character_profile_version: int | None = Field(default=None, ge=1)
    effective_identity_hash: str | None = Field(default=None, min_length=64, max_length=64)
    message_count: int = 0
    created_at: str
    updated_at: str


class ChatSession(ChatSessionSummary):
    _revision: int = PrivateAttr(default=0)
    # When only the newest part of the transcript was loaded (WP-5.7): the
    # stored position of the first loaded message. None: ``messages`` is the
    # whole transcript.
    _window_first_position: int | None = PrivateAttr(default=None)
    messages: list[ChatMessage] = Field(default_factory=list)

    @property
    def transcript_is_window(self) -> bool:
        return self._window_first_position is not None


class TranscriptReader(Protocol):
    """Read port for features that need persisted conversation state."""

    def get_session(self, session_id: str) -> ChatSession | None: ...

    def list_sessions(self) -> Any: ...


class ChatSessionMutationPort(TranscriptReader, Protocol):
    """Single-session mutation port shared by Chat and memory snapshots."""

    def _save_session(self, session: ChatSession) -> None: ...

    def delete_messages(self, session_id: str, message_ids: list[str]) -> int: ...


class PromptMemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    memory_id: str
    content: str
    scope: str
    category: str
    revision: int = Field(ge=1)
    source: Literal[
        "character",
        "system",
        "shared_system",
        "memory_v2",
        "shared_memory_v2",
    ] = "system"


class DeliveryCheckpointRecorder(Protocol):
    """Port for persisting voice delivery checkpoints into the owning chat turn."""

    def __call__(self, details: dict[str, Any]) -> None: ...


class AcceptedChatActivityRecorder(Protocol):
    """Observer for accepted user turns; Chat owns persistence, not enrichment.

    Chat ignores whatever the observer returns.
    """

    def __call__(self, session: ChatSession, user_message: ChatMessage) -> object: ...


class LiveVoiceChatPort(Protocol):
    """Live-voice behavior called by Chat through an injected feature port."""

    route_metadata_key: str

    def is_live_voice_message(self, user_message: Any) -> bool: ...

    def build_live_voice_prompt(
        self,
        store: Any,
        session: Any,
        user_message: Any,
        context_items: list[dict[str, Any]] | None,
    ) -> tuple[Any, Any]: ...

    def record_rendered_prompt(self, assembly: Any, rendered: Any) -> None: ...

    def begin_routed_user_message(self, store: Any, session_id: str, request: Any, *, persist: Any) -> Any: ...

    def resolve_generation_route(self, user_message: Any, **kwargs: Any) -> Any: ...

    def resolve_stream_route(self, user_message: Any, **kwargs: Any) -> Any: ...

    def log_provider_route(self, **kwargs: Any) -> None: ...

    def stream_with_retry(
        self,
        stream_factory: Any,
        fallback_factory: Any,
        *,
        provider_id: str | None,
        model_id: str | None,
    ) -> Iterator[Any]: ...

    def is_lmstudio_provider(self, provider: Any) -> bool: ...

    def generate_lmstudio_reply(self, store: Any, session: Any, user_message: Any, **kwargs: Any) -> Any: ...

    def stream_lmstudio_reply(self, store: Any, session: Any, user_message: Any, **kwargs: Any) -> Iterator[Any]: ...

    def stream_low_latency_reply(self, store: Any, session: Any, user_message: Any, **kwargs: Any) -> Iterator[Any]: ...

    def lmstudio_live_voice_options(self, user_message: Any) -> dict[str, Any]: ...

    def observe_live_voice_provider_stream(self, response: Any) -> Iterator[Any]: ...

    def new_text_chunker(self) -> Any: ...


def estimate_tokens(text: str) -> int:
    """Conservative, provider-independent estimate for UTF-8 text."""
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)
