"""Deterministic scoped selection for approved Chat memory."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.memory_contracts import MemoryRecord, MemoryScopeContext
from .policy import prompt_eligibility

_CATEGORY_PRIORITY = {
    "instruction": 0,
    "preference": 1,
    "project": 2,
    "fact": 3,
    "relationship": 4,
}


class MemorySelectionDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    token_estimate: int = Field(ge=0)
    token_budget: int = Field(ge=0)
    excluded_reason_counts: dict[str, int] = Field(default_factory=dict)
    truncated: bool = False


class MemorySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[MemoryRecord]
    diagnostics: MemorySelectionDiagnostics


def estimate_memory_tokens(content: str) -> int:
    if not content:
        return 0
    return max(1, (len(content.encode("utf-8")) + 3) // 4)


def _updated_timestamp(record: MemoryRecord) -> float:
    try:
        return datetime.fromisoformat(record.updated_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _selection_key(record: MemoryRecord) -> tuple[object, ...]:
    return (
        0 if record.pinned else 1,
        _CATEGORY_PRIORITY.get(record.category, 99),
        -record.confidence,
        -_updated_timestamp(record),
        record.id,
    )


def select_memory_records(
    records: list[MemoryRecord],
    context: MemoryScopeContext,
    *,
    token_budget: int,
) -> MemorySelection:
    excluded: dict[str, int] = {}
    eligible: list[MemoryRecord] = []
    for record in records:
        decision = prompt_eligibility(record, context)
        if decision.allowed:
            eligible.append(record)
        else:
            excluded[decision.reason] = excluded.get(decision.reason, 0) + 1

    selected: list[MemoryRecord] = []
    tokens = 0
    truncated = False
    for record in sorted(eligible, key=_selection_key):
        cost = estimate_memory_tokens(record.content)
        if tokens + cost > max(0, token_budget):
            excluded["token_budget"] = excluded.get("token_budget", 0) + 1
            truncated = True
            continue
        selected.append(record)
        tokens += cost

    return MemorySelection(
        records=selected,
        diagnostics=MemorySelectionDiagnostics(
            candidate_count=len(records),
            selected_count=len(selected),
            token_estimate=tokens,
            token_budget=max(0, token_budget),
            excluded_reason_counts=excluded,
            truncated=truncated,
        ),
    )
