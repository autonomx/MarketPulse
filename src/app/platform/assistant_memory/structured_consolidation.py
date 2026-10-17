"""Deterministic consolidation for structured memory proposals."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.conversation.memory_contracts import MemoryCandidate, MemoryRecord, MemoryScopeContext
from .owner_service import OwnerAwareMemoryService
from .structured_extraction import StructuredMemoryProposal
from .typed_memory import create_typed_memory, supersede_typed_memory


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist_candidate_metadata(
    service: OwnerAwareMemoryService,
    candidate: MemoryCandidate,
    proposal: StructuredMemoryProposal,
) -> MemoryCandidate:
    changed = candidate.model_copy(
        update={
            "proposed_kind": proposal.kind,
            "proposed_payload": proposal.payload,
            "extraction_metadata": {
                **candidate.extraction_metadata,
                "claim_type": proposal.claim_type,
                "evidence_message_ids": proposal.evidence_message_ids,
                "contradiction_key": proposal.contradiction_key,
                "extractor": proposal.extractor,
            },
        }
    )
    repository = service.repository
    database = getattr(repository, "database", None)
    workspace_id = getattr(repository, "workspace_id", None)
    if database is not None and workspace_id:
        with database.transaction() as connection:
            connection.execute(
                """
                UPDATE omnix_memory_candidates
                   SET proposed_kind = %s, proposed_payload = %s::jsonb,
                       extraction_metadata = %s::jsonb
                 WHERE id = %s AND workspace_id = %s
                """,
                (
                    proposal.kind,
                    json.dumps(proposal.payload, sort_keys=True),
                    json.dumps(changed.extraction_metadata, sort_keys=True),
                    candidate.id,
                    workspace_id,
                ),
            )
        stored = repository.get_candidate(candidate.id)
        if stored is None:
            raise RuntimeError("structured candidate disappeared after persistence")
        return stored
    state = getattr(repository, "_state", None)
    if state is not None:
        with state.lock:
            state.candidates[candidate.id] = changed
        return changed
    return changed


def _mark_automatic_direct_assertion(
    service: OwnerAwareMemoryService,
    record: MemoryRecord,
) -> MemoryRecord:
    payload = {**record.structured_payload, "automatic_direct_assertion": True}
    repository = service.repository
    changed = record.model_copy(update={"structured_payload": payload})
    return repository.update_record(changed, expected_revision=record.revision)


def propose_structured_memory(
    service: OwnerAwareMemoryService,
    context: MemoryScopeContext,
    proposal: StructuredMemoryProposal,
    *,
    source_session_id: str,
    source_message_id: str,
) -> MemoryCandidate:
    candidate = service.propose_memory(
        context,
        source_session_id=source_session_id,
        source_message_id=source_message_id,
        scope=proposal.scope,
        category=proposal.category,
        content=proposal.content,
        confidence=proposal.confidence,
        extraction_metadata={
            "claim_type": proposal.claim_type,
            "extractor": proposal.extractor,
        },
    )
    return _persist_candidate_metadata(service, candidate, proposal)


def approve_structured_candidate(
    service: OwnerAwareMemoryService,
    context: MemoryScopeContext,
    candidate_id: str,
) -> MemoryRecord:
    candidate = service.repository.get_candidate(candidate_id)
    if candidate is None:
        raise KeyError(candidate_id)
    record = service.approve_candidate(context, candidate_id)
    payload = candidate.proposed_payload
    repository = service.repository
    contradiction_key = candidate.extraction_metadata.get("contradiction_key")
    changed = record.model_copy(
        update={
            "kind": candidate.proposed_kind,
            "structured_payload": payload,
            "contradiction_group": contradiction_key,
        }
    )
    return repository.update_record(changed, expected_revision=record.revision)


def _matching_records(
    service: OwnerAwareMemoryService,
    context: MemoryScopeContext,
    proposal: StructuredMemoryProposal,
) -> list[MemoryRecord]:
    records = service.list_active(context)
    normalized = proposal.content.casefold()
    exact = [item for item in records if item.content.casefold() == normalized]
    if exact:
        return exact
    if proposal.contradiction_key:
        return [
            item
            for item in records
            if item.contradiction_group == proposal.contradiction_key
            or item.structured_payload.get("contradiction_key")
            == proposal.contradiction_key
        ]
    return []


def consolidate_structured_proposal(
    service: OwnerAwareMemoryService,
    context: MemoryScopeContext,
    proposal: StructuredMemoryProposal,
    *,
    source_session_id: str,
    source_message_id: str,
    auto_save_direct_assertions: bool = False,
) -> tuple[str, MemoryRecord | MemoryCandidate]:
    """Suppress duplicates, supersede contradictions, or create a review candidate."""

    matches = _matching_records(service, context, proposal)
    exact = next(
        (item for item in matches if item.content.casefold() == proposal.content.casefold()),
        None,
    )
    if exact is not None:
        if exact.kind == "routine":
            payload = dict(exact.structured_payload)
            payload["evidence_count"] = int(payload.get("evidence_count") or 1) + 1
            payload["last_observed_at"] = _utcnow()
            changed = exact.model_copy(update={"structured_payload": payload, "updated_at": _utcnow()})
            exact = service.repository.update_record(changed, expected_revision=exact.revision)
        return "duplicate_merged", exact

    contradiction = next((item for item in matches if item.id != (exact.id if exact else "")), None)
    if contradiction is not None and proposal.claim_type in {
        "explicit_command",
        "user_asserted",
    }:
        replacement = supersede_typed_memory(
            service,
            context,
            contradiction.id,
            kind=proposal.kind,
            content=proposal.content,
            payload=proposal.payload,
            provenance_id=source_message_id,
        )
        return "superseded", replacement

    automatic_direct_assertion = (
        auto_save_direct_assertions and proposal.claim_type == "user_asserted"
    )
    if proposal.claim_type == "explicit_command" or automatic_direct_assertion:
        record = create_typed_memory(
            service,
            context,
            kind=proposal.kind,
            content=proposal.content,
            payload=proposal.payload,
            scope=proposal.scope,
            category=proposal.category,
            provenance_id=source_message_id,
            contradiction_group=proposal.contradiction_key,
        )
        if automatic_direct_assertion:
            record = _mark_automatic_direct_assertion(service, record)
        return "saved", record

    candidate = propose_structured_memory(
        service,
        context,
        proposal,
        source_session_id=source_session_id,
        source_message_id=source_message_id,
    )
    return "proposed", candidate


__all__ = [
    "approve_structured_candidate",
    "consolidate_structured_proposal",
    "propose_structured_memory",
]
