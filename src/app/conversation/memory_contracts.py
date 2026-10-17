"""Typed contracts for curated Chat and Character memory."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryScope = Literal["global", "workspace", "project", "session"]
MemoryOwnerType = Literal["system", "character"]
MemoryCategory = Literal["preference", "fact", "project", "relationship", "instruction"]
MemoryKind = Literal[
    "semantic_fact",
    "preference",
    "instruction",
    "relationship_state",
    "episode",
    "routine",
    "goal",
    "open_loop",
    "temporal_fact",
    "pronunciation",
]
MemorySource = Literal["user_saved", "assistant_suggested", "imported", "hermes"]
MemoryCandidateStatus = Literal["pending", "rejected", "accepted"]
MemoryRecordStatus = Literal["active", "superseded", "archived"]
MemoryTrustLevel = Literal["user_approved", "system_trusted", "unverified_import", "unverified_agent", "external_untrusted"]
MemorySensitivity = Literal["normal", "sensitive", "secret"]
MemoryProvenanceType = Literal["user_message", "assistant_inference", "import", "hermes", "system"]

SYSTEM_MEMORY_OWNER_ID = "system-assistant"

# Trust levels whose records may reach a prompt (anything else needs approval).
PROMPT_TRUST_LEVELS: frozenset[str] = frozenset({"user_approved", "system_trusted"})


class MemoryConflictError(RuntimeError):
    """Raised when an optimistic revision or pending-state check fails."""


class MemoryNotFoundError(KeyError):
    """Raised when a requested memory entity does not exist."""


class MemoryScopeContext(BaseModel):
    """Backend-resolved owner and scope identity used for every policy decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_id: str = Field(min_length=1, max_length=160)
    workspace_id: str = Field(min_length=1, max_length=160)
    project_id: str | None = Field(default=None, max_length=160)
    session_id: str = Field(min_length=1, max_length=200)
    owner_type: MemoryOwnerType = "system"
    owner_id: str = Field(default=SYSTEM_MEMORY_OWNER_ID, min_length=1, max_length=160)


class MemoryRecord(BaseModel):
    """Approved or otherwise active curated memory owned by Omnix."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    owner_type: MemoryOwnerType = "system"
    owner_id: str = Field(default=SYSTEM_MEMORY_OWNER_ID, min_length=1, max_length=160)
    scope: MemoryScope
    scope_id: str = Field(min_length=1, max_length=200)
    category: MemoryCategory
    kind: MemoryKind = "semantic_fact"
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    supersedes_memory_id: str | None = Field(default=None, max_length=200)
    contradiction_group: str | None = Field(default=None, max_length=200)
    source: MemorySource
    content: str = Field(min_length=1, max_length=4096)
    normalized_content: str = Field(min_length=1, max_length=4096)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    pinned: bool = False
    trust_level: MemoryTrustLevel = "user_approved"
    sensitivity: MemorySensitivity = "normal"
    provenance_type: MemoryProvenanceType
    provenance_id: str | None = Field(default=None, max_length=240)
    status: MemoryRecordStatus = "active"
    revision: int = Field(default=1, ge=1)
    created_at: str
    updated_at: str
    expires_at: str | None = None


class MemoryCandidate(BaseModel):
    """Non-prompt-eligible proposal awaiting an explicit resolution."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    owner_type: MemoryOwnerType = "system"
    owner_id: str = Field(default=SYSTEM_MEMORY_OWNER_ID, min_length=1, max_length=160)
    source_session_id: str = Field(min_length=1, max_length=200)
    source_message_id: str = Field(min_length=1, max_length=200)
    candidate_fingerprint: str = Field(min_length=1, max_length=200)
    proposed_scope: MemoryScope
    proposed_scope_id: str = Field(min_length=1, max_length=200)
    proposed_category: MemoryCategory
    proposed_kind: MemoryKind = "semantic_fact"
    proposed_payload: dict[str, Any] = Field(default_factory=dict)
    proposed_supersedes_memory_id: str | None = Field(default=None, max_length=200)
    proposed_content: str = Field(min_length=1, max_length=4096)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: MemorySource = "assistant_suggested"
    trust_level: MemoryTrustLevel = "unverified_agent"
    sensitivity: MemorySensitivity = "normal"
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)
    status: MemoryCandidateStatus = "pending"
    created_at: str
    resolved_at: str | None = None


class MemorySnapshotItem(BaseModel):
    """Frozen snapshot copy with revocation able to override immutability."""

    model_config = ConfigDict(extra="forbid")
    memory_record_id: str = Field(min_length=1, max_length=200)
    record_revision: int = Field(ge=1)
    frozen_content: str = Field(min_length=1, max_length=4096)
    revoked_at: str | None = None


# Snapshots are chosen by token budget; this bound keeps every read of a
# snapshot's items bounded (WP-5.5).
MAX_MEMORY_SNAPSHOT_ITEMS = 5000


class MemorySnapshot(BaseModel):
    """Stable per-session, per-owner memory selection."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    owner_type: MemoryOwnerType = "system"
    owner_id: str = Field(default=SYSTEM_MEMORY_OWNER_ID, min_length=1, max_length=160)
    revision: int = Field(default=1, ge=1)
    items: list[MemorySnapshotItem] = Field(default_factory=list, max_length=MAX_MEMORY_SNAPSHOT_ITEMS)
    token_estimate: int = Field(default=0, ge=0)
    created_at: str
    refreshed_at: str | None = None


class MemoryPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    allowed: bool
    reason: str = Field(min_length=1, max_length=160)


def _parse_record_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def record_is_expired(record: MemoryRecord, now: datetime | None = None) -> bool:
    if not record.expires_at:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return _parse_record_time(record.expires_at) <= current


def record_prompt_block_reason(record: MemoryRecord, now: datetime | None = None) -> str | None:
    """Why this record may not reach a prompt, whatever the scope; ``None`` if it may.

    One rule for Memory v1 selection and for what Memory v2 makes retrievable.
    """
    if record.status != "active":
        return f"record_{record.status}"
    if record_is_expired(record, now):
        return "record_expired"
    if record.sensitivity == "secret":
        return "secret_content_blocked"
    if record.trust_level not in PROMPT_TRUST_LEVELS:
        return "trust_not_approved"
    return None
