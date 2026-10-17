"""Durable, idempotent post-turn structured memory jobs."""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from app.jobs import (
    CompleteJobRequest,
    CreateJobRequest,
    JobRecord,
    ResourceClass,
    default_job_store,
)

from app.conversation.memory_contracts import MemoryCandidate, MemoryRecord
from .owner_defaults import default_memory_service
from .rollout import companion_rollout_policy
from .scope import resolve_session_memory_scope
from .service import MemoryService
from .settings import load_memory_runtime_settings
from .structured_consolidation import consolidate_structured_proposal
from .structured_extraction import extract_structured_memory_proposals
from .structured_provider import StructuredProposalProvider

if TYPE_CHECKING:
    from app.conversation.contracts import TranscriptReader

MEMORY_SUGGEST_JOB_TYPE = "assistant.memory.suggest"
MEMORY_IMPORT_JOB_TYPE = "assistant.memory.import"
MEMORY_CONSOLIDATE_JOB_TYPE = "assistant.memory.consolidate"
HISTORY_COMPACT_JOB_TYPE = "assistant.history.compact"


class MemorySuggestionJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: str
    user_message_id: str
    idempotency_key: str


class MemorySuggestionJobResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    candidate_ids: list[str] = Field(default_factory=list)
    record_ids: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    skipped_reasons: list[str] = Field(default_factory=list)


def memory_suggestions_enabled() -> bool:
    settings = load_memory_runtime_settings()
    policy = companion_rollout_policy(settings)
    return (
        settings.suggestions_enabled and policy.review_candidates_enabled
    ) or policy.automatic_direct_assertions_enabled


def suggestion_idempotency_key(session_id: str, user_message_id: str) -> str:
    return hashlib.sha256(
        f"{MEMORY_SUGGEST_JOB_TYPE}\n{session_id}\n{user_message_id}".encode("utf-8")
    ).hexdigest()


def create_memory_suggestion_job_request(
    session_id: str,
    user_message_id: str,
) -> CreateJobRequest:
    key = suggestion_idempotency_key(session_id, user_message_id)
    return CreateJobRequest(
        owner_id=session_id,
        module="assistant-memory",
        type=MEMORY_SUGGEST_JOB_TYPE,
        resource_class=ResourceClass.CPU,
        input_payload=MemorySuggestionJobInput(
            session_id=session_id,
            user_message_id=user_message_id,
            idempotency_key=key,
        ).model_dump(mode="json"),
        compat={"contract": "assistant_memory_suggestion_v2", "idempotency_key": key},
    )


def enqueue_memory_suggestion_job(
    session_id: str,
    user_message_id: str,
    *,
    job_store: Any | None = None,
) -> JobRecord | None:
    if not memory_suggestions_enabled():
        return None
    store = job_store or default_job_store()
    key = suggestion_idempotency_key(session_id, user_message_id)
    create_once = getattr(store, "create_job_once", None)
    if callable(create_once):
        return create_once(
            create_memory_suggestion_job_request(session_id, user_message_id),
            idempotency_key=key,
        )
    for job in store.iter_jobs(job_types=(MEMORY_SUGGEST_JOB_TYPE,)):
        if job.type == MEMORY_SUGGEST_JOB_TYPE and job.compat.get("idempotency_key") == key:
            return job
    return store.create_job(create_memory_suggestion_job_request(session_id, user_message_id))


def extract_memory_candidates(content: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Compatibility adapter exposing the deterministic fallback as dictionaries."""

    proposals, skipped = extract_structured_memory_proposals(
        content,
        source_message_id="compat:message",
    )
    return [proposal.model_dump(mode="json") for proposal in proposals], skipped


def _complete_result(
    result: MemorySuggestionJobResult,
    *,
    store: Any,
) -> None:
    output_refs = [
        {"type": "memory_candidate", "id": candidate_id}
        for candidate_id in result.candidate_ids
    ] + [
        {"type": "memory_record", "id": record_id}
        for record_id in result.record_ids
    ]
    store.complete_job(
        result.job_id,
        CompleteJobRequest(
            output_refs=output_refs,
            logs=[
                {
                    "event": "memory.suggestion.processed",
                    "job_id": result.job_id,
                    "candidate_count": len(result.candidate_ids),
                    "record_count": len(result.record_ids),
                    "actions": result.actions,
                    "skipped_reasons": result.skipped_reasons,
                }
            ],
        ),
    )


def process_memory_suggestion_job(
    job: JobRecord,
    *,
    chat_store: TranscriptReader,
    memory_service: MemoryService | None = None,
    job_store: Any | None = None,
    proposal_provider: StructuredProposalProvider | None = None,
    already_claimed: bool = False,
) -> MemorySuggestionJobResult:
    if job.type != MEMORY_SUGGEST_JOB_TYPE:
        raise ValueError(f"unsupported memory job type: {job.type}")
    from app.platform.assistant_memory.persistence.memory_job_execution import MemoryJobExecution

    with MemoryJobExecution(
        job_store or default_job_store(),
        job,
        already_claimed=already_claimed,
    ) as execution:
        if not execution.claimed:
            return MemorySuggestionJobResult(
                job_id=job.id, skipped_reasons=["job_not_claimable"],
            )
        return _process_claimed_memory_job(
            execution.job, chat_store=chat_store, memory_service=memory_service,
            proposal_provider=proposal_provider, execution=execution,
        )


def _process_claimed_memory_job(
    job, *, chat_store, memory_service, proposal_provider, execution,
) -> MemorySuggestionJobResult:
    from app.conversation.privacy import automatic_memory_derivation_allowed

    payload = MemorySuggestionJobInput.model_validate(job.input_payload or {})
    session = chat_store.get_session(payload.session_id)
    result = MemorySuggestionJobResult(job_id=job.id)
    settings = load_memory_runtime_settings()
    rollout = companion_rollout_policy(settings)
    service = None
    proposals: list[Any] = []
    if not rollout.review_candidates_enabled and not rollout.automatic_direct_assertions_enabled:
        result.skipped_reasons.append("rollout_stage_disabled")
    elif session is None:
        result.skipped_reasons.append("session_missing")
    elif not automatic_memory_derivation_allowed(session):
        result.skipped_reasons.append("private_session")
    elif session.interaction_mode == "character" and not session.write_memory:
        result.skipped_reasons.append("character_memory_write_disabled")
    else:
        message = next(
            (
                item
                for item in session.messages
                if item.id == payload.user_message_id and item.role == "user"
            ),
            None,
        )
        if message is None:
            result.skipped_reasons.append("user_message_missing")
        elif message.metadata.get("memory_command"):
            result.skipped_reasons.append("explicit_memory_command")
        else:
            service = memory_service or default_memory_service()
            context = resolve_session_memory_scope(session)
            proposals, skipped = extract_structured_memory_proposals(
                message.content,
                source_message_id=message.id,
                proposal_provider=proposal_provider,
            )
            result.skipped_reasons.extend(skipped)
    with execution.write_result(service):
        if service is not None and session is not None and message is not None:
            for proposal in proposals:
                action, entity = consolidate_structured_proposal(
                    service,
                    context,
                    proposal,
                    source_session_id=session.id,
                    source_message_id=message.id,
                    auto_save_direct_assertions=(
                        rollout.automatic_direct_assertions_enabled
                    ),
                )
                result.actions.append(action)
                if isinstance(entity, MemoryCandidate):
                    result.candidate_ids.append(entity.id)
                elif isinstance(entity, MemoryRecord):
                    result.record_ids.append(entity.id)

        _complete_result(result, store=execution)
    return result


__all__ = [
    "HISTORY_COMPACT_JOB_TYPE",
    "MEMORY_CONSOLIDATE_JOB_TYPE",
    "MEMORY_IMPORT_JOB_TYPE",
    "MEMORY_SUGGEST_JOB_TYPE",
    "MemorySuggestionJobInput",
    "MemorySuggestionJobResult",
    "create_memory_suggestion_job_request",
    "enqueue_memory_suggestion_job",
    "extract_memory_candidates",
    "memory_suggestions_enabled",
    "process_memory_suggestion_job",
]
