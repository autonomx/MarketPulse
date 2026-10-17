"""Pure safety and scope policy for Chat memory."""
from __future__ import annotations

from datetime import datetime

from app.conversation.memory_contracts import (
    PROMPT_TRUST_LEVELS,
    MemoryCandidate,
    MemoryPolicyDecision,
    MemoryRecord,
    MemoryScope,
    MemoryScopeContext,
    MemorySource,
    record_is_expired,
    record_prompt_block_reason,
)
from .scope import scope_id_for

_PROMPT_TRUST_LEVELS = PROMPT_TRUST_LEVELS
_APPROVAL_REQUIRED_SOURCES: set[MemorySource] = {
    "assistant_suggested",
    "imported",
    "hermes",
}


def source_requires_approval(source: MemorySource) -> bool:
    return source in _APPROVAL_REQUIRED_SOURCES


def is_visible_in_scope(record: MemoryRecord, context: MemoryScopeContext) -> bool:
    expected_scope_id = scope_id_for(record.scope, context)
    return expected_scope_id is not None and record.scope_id == expected_scope_id


def is_expired(record: MemoryRecord, now: datetime | None = None) -> bool:
    return record_is_expired(record, now)


def prompt_eligibility(
    record: MemoryRecord,
    context: MemoryScopeContext,
    *,
    now: datetime | None = None,
) -> MemoryPolicyDecision:
    if record.status != "active":
        return MemoryPolicyDecision(allowed=False, reason=f"record_{record.status}")
    if not is_visible_in_scope(record, context):
        return MemoryPolicyDecision(allowed=False, reason="scope_mismatch")
    reason = record_prompt_block_reason(record, now)
    if reason is not None:
        return MemoryPolicyDecision(allowed=False, reason=reason)
    return MemoryPolicyDecision(allowed=True, reason="approved_memory")


def candidate_acceptance(candidate: MemoryCandidate) -> MemoryPolicyDecision:
    if candidate.status != "pending":
        return MemoryPolicyDecision(allowed=False, reason=f"candidate_{candidate.status}")
    if candidate.sensitivity == "secret":
        return MemoryPolicyDecision(allowed=False, reason="secret_content_blocked")
    if candidate.trust_level in _PROMPT_TRUST_LEVELS:
        return MemoryPolicyDecision(allowed=False, reason="candidate_cannot_self_approve")
    return MemoryPolicyDecision(allowed=True, reason="candidate_requires_user_approval")


def move_scope_decision(
    record: MemoryRecord,
    target_scope: MemoryScope,
    context: MemoryScopeContext,
) -> MemoryPolicyDecision:
    if not is_visible_in_scope(record, context):
        return MemoryPolicyDecision(allowed=False, reason="source_scope_mismatch")
    if scope_id_for(target_scope, context) is None:
        return MemoryPolicyDecision(allowed=False, reason="target_scope_unavailable")
    if record.sensitivity == "secret":
        return MemoryPolicyDecision(allowed=False, reason="secret_content_blocked")
    return MemoryPolicyDecision(allowed=True, reason="scope_move_allowed")


def explicit_save_decision(*, sensitivity: str, content_source: str) -> MemoryPolicyDecision:
    if sensitivity == "secret":
        return MemoryPolicyDecision(allowed=False, reason="secret_content_blocked")
    if content_source in {"web", "email", "document", "tool", "repository", "hermes_observation"}:
        return MemoryPolicyDecision(allowed=False, reason="external_content_requires_review")
    if content_source != "user_message":
        return MemoryPolicyDecision(allowed=False, reason="unsupported_content_source")
    return MemoryPolicyDecision(allowed=True, reason="explicit_user_save")
