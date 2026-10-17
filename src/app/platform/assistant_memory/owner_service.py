"""Owner-isolated extension of the canonical Omnix memory service."""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from app.conversation.memory_contracts import (
    MemoryCandidate,
    MemoryCandidateStatus,
    MemoryCategory,
    MemoryRecord,
    MemoryScope,
    MemoryScopeContext,
    MemorySensitivity,
    MemorySnapshot,
    MemorySnapshotItem,
    MemorySource,
)
from .owner_repository import OwnerAwareInMemoryMemoryRepository
from .policy import (
    candidate_acceptance,
    explicit_save_decision,
    is_visible_in_scope,
    source_requires_approval,
)
from .repository import MemoryNotFoundError
from .scope import scope_id_for
from .selection import MemorySelection, select_memory_records
from .service import MemoryPolicyError, MemoryService, normalize_memory_content


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(
    context: MemoryScopeContext,
    source_message_id: str,
    scope: MemoryScope,
    scope_id: str,
    category: MemoryCategory,
    content: str,
) -> str:
    canonical = "\n".join([
        context.owner_type,
        context.owner_id,
        source_message_id,
        scope,
        scope_id,
        category,
        normalize_memory_content(content),
    ])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class OwnerAwareMemoryService(MemoryService):
    def __init__(
        self,
        repository: OwnerAwareInMemoryMemoryRepository | None = None,
        *,
        write_guard: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            repository or OwnerAwareInMemoryMemoryRepository(),
            write_guard=write_guard,
        )

    @property
    def owner_repository(self) -> OwnerAwareInMemoryMemoryRepository:
        return self.repository  # type: ignore[return-value]

    def list_active(self, context: MemoryScopeContext) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        seen: set[str] = set()
        for scope in ("global", "workspace", "project", "session"):
            scope_id = scope_id_for(scope, context)
            if scope_id is None:
                continue
            for record in self.owner_repository.list_records(
                owner_type=context.owner_type,
                owner_id=context.owner_id,
                scope=scope,
                scope_id=scope_id,
            ):
                if record.id not in seen:
                    seen.add(record.id)
                    records.append(record)
        return records

    def create_explicit_memory(
        self,
        context: MemoryScopeContext,
        *,
        scope: MemoryScope,
        category: MemoryCategory,
        content: str,
        provenance_id: str | None,
        pinned: bool = False,
        sensitivity: MemorySensitivity = "normal",
    ) -> MemoryRecord:
        self._assert_write_allowed()
        decision = explicit_save_decision(
            sensitivity=sensitivity,
            content_source="user_message",
        )
        if not decision.allowed:
            raise MemoryPolicyError(decision.reason)
        scope_id = scope_id_for(scope, context)
        if scope_id is None:
            raise MemoryPolicyError("target_scope_unavailable")
        normalized = normalize_memory_content(content)
        if not normalized:
            raise MemoryPolicyError("memory_content_empty")
        now = _utcnow()
        return self.owner_repository.create_record(
            MemoryRecord(
                id=f"memory:{uuid.uuid4().hex}",
                owner_type=context.owner_type,
                owner_id=context.owner_id,
                scope=scope,
                scope_id=scope_id,
                category=category,
                source="user_saved",
                content=content.strip(),
                normalized_content=normalized,
                confidence=1.0,
                pinned=pinned,
                trust_level="user_approved",
                sensitivity=sensitivity,
                provenance_type="user_message",
                provenance_id=provenance_id,
                created_at=now,
                updated_at=now,
            )
        )

    def propose_memory(
        self,
        context: MemoryScopeContext,
        *,
        source_session_id: str,
        source_message_id: str,
        scope: MemoryScope,
        category: MemoryCategory,
        content: str,
        confidence: float,
        source: MemorySource = "assistant_suggested",
        sensitivity: MemorySensitivity = "normal",
        extraction_metadata: dict[str, object] | None = None,
    ) -> MemoryCandidate:
        self._assert_write_allowed()
        if not source_requires_approval(source):
            raise MemoryPolicyError("candidate_source_does_not_require_approval")
        scope_id = scope_id_for(scope, context)
        if scope_id is None:
            raise MemoryPolicyError("target_scope_unavailable")
        normalized = normalize_memory_content(content)
        if not normalized:
            raise MemoryPolicyError("memory_content_empty")
        now = _utcnow()
        return self.owner_repository.create_candidate(
            MemoryCandidate(
                id=f"candidate:{uuid.uuid4().hex}",
                owner_type=context.owner_type,
                owner_id=context.owner_id,
                source_session_id=source_session_id,
                source_message_id=source_message_id,
                candidate_fingerprint=_fingerprint(
                    context,
                    source_message_id,
                    scope,
                    scope_id,
                    category,
                    content,
                ),
                proposed_scope=scope,
                proposed_scope_id=scope_id,
                proposed_category=category,
                proposed_content=content.strip(),
                confidence=confidence,
                source=source,
                trust_level=(
                    "unverified_agent"
                    if source in {"assistant_suggested", "hermes"}
                    else "unverified_import"
                ),
                sensitivity=sensitivity,
                extraction_metadata=dict(extraction_metadata or {}),
                created_at=now,
            )
        )

    def approve_candidate(
        self,
        context: MemoryScopeContext,
        candidate_id: str,
        *,
        pinned: bool = False,
    ) -> MemoryRecord:
        self._assert_write_allowed()
        candidate = self.owner_repository.get_candidate(candidate_id)
        if candidate is None:
            raise MemoryNotFoundError(candidate_id)
        if (candidate.owner_type, candidate.owner_id) != (context.owner_type, context.owner_id):
            raise MemoryPolicyError("owner_mismatch")
        decision = candidate_acceptance(candidate)
        if not decision.allowed:
            raise MemoryPolicyError(decision.reason)
        expected_scope_id = scope_id_for(candidate.proposed_scope, context)
        if expected_scope_id != candidate.proposed_scope_id:
            raise MemoryPolicyError("candidate_scope_mismatch")
        now = _utcnow()
        record = MemoryRecord(
            id=f"memory:{uuid.uuid4().hex}",
            owner_type=context.owner_type,
            owner_id=context.owner_id,
            scope=candidate.proposed_scope,
            scope_id=candidate.proposed_scope_id,
            category=candidate.proposed_category,
            source=candidate.source,
            content=candidate.proposed_content,
            normalized_content=normalize_memory_content(candidate.proposed_content),
            confidence=candidate.confidence,
            pinned=pinned,
            trust_level="user_approved",
            sensitivity=candidate.sensitivity,
            provenance_type=(
                "hermes"
                if candidate.source == "hermes"
                else "import"
                if candidate.source == "imported"
                else "assistant_inference"
            ),
            provenance_id=candidate.source_message_id,
            created_at=now,
            updated_at=now,
        )
        return self.owner_repository.accept_candidate(candidate.id, record, resolved_at=now)

    def delete_resolved_candidate(
        self,
        context: MemoryScopeContext,
        candidate_id: str,
        *,
        expected_status: MemoryCandidateStatus,
    ) -> bool:
        self._assert_write_allowed()
        if expected_status == "pending":
            raise MemoryPolicyError("candidate_cleanup_requires_resolved_status")
        candidate = self.owner_repository.get_candidate(candidate_id)
        if candidate is None:
            raise MemoryNotFoundError(candidate_id)
        if (candidate.owner_type, candidate.owner_id) != (context.owner_type, context.owner_id):
            raise MemoryPolicyError("owner_mismatch")
        if candidate.status != expected_status:
            raise MemoryPolicyError("candidate_status_mismatch")
        expected_scope_id = scope_id_for(candidate.proposed_scope, context)
        if expected_scope_id != candidate.proposed_scope_id:
            raise MemoryPolicyError("candidate_scope_mismatch")
        return self.owner_repository.delete_candidate(
            candidate_id,
            expected_status=expected_status,
        )

    def resolve_active_memory(
        self,
        context: MemoryScopeContext,
        *,
        token_budget: int,
    ) -> MemorySelection:
        return select_memory_records(
            self.list_active(context),
            context,
            token_budget=token_budget,
        )

    def create_session_snapshot(
        self,
        context: MemoryScopeContext,
        *,
        token_budget: int,
        refresh: bool = False,
    ) -> MemorySnapshot:
        self._assert_write_allowed()
        selection = self.resolve_active_memory(context, token_budget=token_budget)
        previous = self.owner_repository.latest_snapshot(
            context.session_id,
            owner_type=context.owner_type,
            owner_id=context.owner_id,
        )
        now = _utcnow()
        snapshot = MemorySnapshot(
            id=f"memory-snapshot:{uuid.uuid4().hex}",
            session_id=context.session_id,
            owner_type=context.owner_type,
            owner_id=context.owner_id,
            revision=(previous.revision + 1) if previous else 1,
            items=[
                MemorySnapshotItem(
                    memory_record_id=record.id,
                    record_revision=record.revision,
                    frozen_content=record.content,
                )
                for record in selection.records
            ],
            token_estimate=selection.diagnostics.token_estimate,
            created_at=now,
            refreshed_at=now if refresh else None,
        )
        return self.owner_repository.create_snapshot(snapshot)

    def _visible_record(self, context: MemoryScopeContext, record_id: str) -> MemoryRecord:
        record = self.owner_repository.get_record(record_id)
        if record is None:
            raise MemoryNotFoundError(record_id)
        if (record.owner_type, record.owner_id) != (context.owner_type, context.owner_id):
            raise MemoryPolicyError("owner_mismatch")
        if not is_visible_in_scope(record, context):
            raise MemoryPolicyError("scope_mismatch")
        return record


__all__ = ["OwnerAwareMemoryService"]
