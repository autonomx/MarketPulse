"""Memory health, contradiction, consolidation, and capacity policy."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.memory_contracts import MemoryRecord, MemoryScopeContext
from .policy import is_expired, is_visible_in_scope
from .repository import MemoryConflictError
from .selection import estimate_memory_tokens
from .service import MemoryPolicyError, MemoryService

_FACT_PATTERN = re.compile(r"^(.{1,120}?)\s+is\s+(.{1,300})$", re.IGNORECASE)


class MemoryCapacityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_records_per_scope: int = Field(default=200, ge=1, le=10_000)
    soft_token_budget: int = Field(default=4_000, ge=0)
    hard_token_ceiling: int = Field(default=8_000, ge=1)


class MemoryDuplicateGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normalized_content: str
    memory_ids: list[str]


class MemoryContradictionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    memory_ids: list[str]
    values: list[str]


class MemoryHealthReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visible_record_count: int = Field(ge=0)
    active_record_count: int = Field(ge=0)
    prompt_eligible_token_estimate: int = Field(ge=0)
    duplicate_groups: list[MemoryDuplicateGroup] = Field(default_factory=list)
    contradiction_groups: list[MemoryContradictionGroup] = Field(default_factory=list)
    expired_memory_ids: list[str] = Field(default_factory=list)
    untrusted_memory_ids: list[str] = Field(default_factory=list)
    over_record_limit_scopes: list[str] = Field(default_factory=list)
    soft_budget_exceeded: bool = False
    hard_ceiling_exceeded: bool = False
    consolidation_required: bool = False


def _fact_subject_value(record: MemoryRecord) -> tuple[str, str] | None:
    if record.category != "fact":
        return None
    match = _FACT_PATTERN.match(record.normalized_content)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def analyze_memory_health(
    records: list[MemoryRecord],
    context: MemoryScopeContext,
    *,
    policy: MemoryCapacityPolicy | None = None,
) -> MemoryHealthReport:
    capacity = policy or MemoryCapacityPolicy()
    visible = [record for record in records if is_visible_in_scope(record, context)]
    active = [record for record in visible if record.status == "active"]

    normalized: dict[str, list[str]] = defaultdict(list)
    facts: dict[str, list[tuple[str, str]]] = defaultdict(list)
    scope_counts: dict[str, int] = defaultdict(int)
    expired: list[str] = []
    untrusted: list[str] = []
    token_estimate = 0
    for record in active:
        normalized[record.normalized_content].append(record.id)
        scope_counts[f"{record.scope}:{record.scope_id}"] += 1
        if is_expired(record):
            expired.append(record.id)
            continue
        if record.trust_level not in {"user_approved", "system_trusted"}:
            untrusted.append(record.id)
            continue
        token_estimate += estimate_memory_tokens(record.content)
        fact = _fact_subject_value(record)
        if fact:
            facts[fact[0]].append((record.id, fact[1]))

    duplicates = [
        MemoryDuplicateGroup(normalized_content=content, memory_ids=sorted(ids))
        for content, ids in sorted(normalized.items())
        if len(ids) > 1
    ]
    contradictions = []
    for subject, values in sorted(facts.items()):
        distinct_values = sorted({value for _, value in values})
        if len(distinct_values) > 1:
            contradictions.append(
                MemoryContradictionGroup(
                    subject=subject,
                    memory_ids=sorted(memory_id for memory_id, _ in values),
                    values=distinct_values,
                )
            )
    over_scopes = sorted(
        scope_key for scope_key, count in scope_counts.items()
        if count > capacity.max_records_per_scope
    )
    required = bool(
        duplicates
        or contradictions
        or expired
        or untrusted
        or over_scopes
        or token_estimate > capacity.soft_token_budget
    )
    return MemoryHealthReport(
        visible_record_count=len(visible),
        active_record_count=len(active),
        prompt_eligible_token_estimate=token_estimate,
        duplicate_groups=duplicates,
        contradiction_groups=contradictions,
        expired_memory_ids=sorted(expired),
        untrusted_memory_ids=sorted(untrusted),
        over_record_limit_scopes=over_scopes,
        soft_budget_exceeded=token_estimate > capacity.soft_token_budget,
        hard_ceiling_exceeded=token_estimate > capacity.hard_token_ceiling,
        consolidation_required=required,
    )


def supersede_memory(
    service: MemoryService,
    context: MemoryScopeContext,
    *,
    older_memory_id: str,
    replacement_memory_id: str,
    expected_revision: int,
) -> MemoryRecord:
    older = service.repository.get_record(older_memory_id)
    replacement = service.repository.get_record(replacement_memory_id)
    if older is None or replacement is None:
        raise MemoryPolicyError("memory_not_found")
    if not is_visible_in_scope(older, context) or not is_visible_in_scope(replacement, context):
        raise MemoryPolicyError("scope_mismatch")
    if older.id == replacement.id:
        raise MemoryPolicyError("replacement_must_differ")
    if older.status != "active" or replacement.status != "active":
        raise MemoryPolicyError("memory_not_active")
    now = datetime.now(timezone.utc).isoformat()
    changed = older.model_copy(
        update={
            "status": "superseded",
            "updated_at": now,
            "provenance_id": replacement.id,
        }
    )
    try:
        return service.repository.update_record(changed, expected_revision=expected_revision)
    except MemoryConflictError:
        raise
