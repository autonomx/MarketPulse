"""Agent run storage: task revisions, evidence receipts and supersession (WP-8.2).

Functions over the run repository; ``PostgresAgentRunRepository`` keeps one
delegator per function, so callers and tests are unchanged.
"""
from __future__ import annotations

from .contracts import (
    AgentEvent,
    AgentRunSnapshot,
    EvidenceReceipt,
    TaskRevision,
)
from typing import Any, TYPE_CHECKING, cast
from .repository import (
    AgentRunConcurrencyError,
    _json,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.repository import PostgresAgentRunRepository


def add_task_revision(repo: PostgresAgentRunRepository, revision: TaskRevision) -> TaskRevision:
    repo.connection.execute(
        "SELECT revision FROM omnix_agent_runs WHERE workspace_id = %s AND run_id = %s FOR UPDATE",
        (repo.context.workspace_id, revision.run_id),
    ).fetchone()
    inserted = repo.connection.execute(
        """
        INSERT INTO omnix_agent_task_revisions (
            workspace_id, run_id, revision_id, sequence, previous_revision_id,
            source_command_id, user_instruction, effective_objective,
            effective_success_criteria, evidence_decision,
            required_local_capabilities, required_external_capabilities,
            expected_artifacts, acceptance_checks, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
            %s::jsonb, %s::jsonb, %s
        )
        ON CONFLICT DO NOTHING
        RETURNING revision_id
        """,
        (
            repo.context.workspace_id,
            revision.run_id,
            revision.revision_id,
            revision.sequence,
            revision.previous_revision_id,
            revision.source_command_id,
            revision.user_instruction,
            revision.effective_objective,
            _json(revision.effective_success_criteria),
            _json(revision.evidence_decision),
            _json(revision.required_local_capabilities),
            _json(revision.required_external_capabilities),
            _json(revision.expected_artifacts),
            _json(revision.acceptance_checks),
            revision.created_at,
        ),
    )
    row = inserted.fetchone()
    if row is None:
        existing = repo.connection.execute(
            """
            SELECT revision_id
              FROM omnix_agent_task_revisions
             WHERE workspace_id = %s AND run_id = %s
               AND (revision_id = %s OR source_command_id = %s)
             LIMIT 1
            """,
            (
                repo.context.workspace_id,
                revision.run_id,
                revision.revision_id,
                revision.source_command_id,
            ),
        ).fetchone()
        if existing is None:
            raise AgentRunConcurrencyError("task revision sequence conflict")
        rows = repo.list_task_revisions(revision.run_id)
        return next(item for item in rows if item.revision_id == str(existing[0]))
    repo.append_event(AgentEvent(
        run_id=revision.run_id,
        event_type="task.revised",
        payload={
            "revision_id": revision.revision_id,
            "sequence": revision.sequence,
            "source_command_id": revision.source_command_id,
            "evidence_reason": revision.evidence_decision.reason,
        },
    ))
    return revision


def list_task_revisions(repo: PostgresAgentRunRepository, run_id: str) -> list[TaskRevision]:
    rows = repo.connection.execute(
        """
        SELECT revision_id, sequence, previous_revision_id, source_command_id,
               user_instruction, effective_objective, effective_success_criteria,
               evidence_decision, required_local_capabilities,
               required_external_capabilities, expected_artifacts,
               acceptance_checks, created_at
          FROM omnix_agent_task_revisions
         WHERE workspace_id = %s AND run_id = %s
         ORDER BY sequence
        """,
        (repo.context.workspace_id, run_id),
    ).fetchall()
    return [
        TaskRevision.model_validate(
            {
                "revision_id": str(row[0]),
                "run_id": run_id,
                "sequence": int(row[1]),
                "previous_revision_id": str(row[2]) if row[2] else None,
                "source_command_id": str(row[3]) if row[3] else None,
                "user_instruction": str(row[4]),
                "effective_objective": str(row[5]),
                "effective_success_criteria": list(row[6] or []),
                "evidence_decision": row[7] or {},
                "required_local_capabilities": list(row[8] or []),
                "required_external_capabilities": list(row[9] or []),
                "expected_artifacts": list(row[10] or []),
                "acceptance_checks": list(row[11] or []),
                "created_at": row[12],
            }
        )
        for row in rows
    ]


def latest_task_revision(repo: PostgresAgentRunRepository, run_id: str) -> TaskRevision | None:
    rows = repo.list_task_revisions(run_id)
    return rows[-1] if rows else None


def add_evidence_receipt(repo: PostgresAgentRunRepository, receipt: EvidenceReceipt) -> EvidenceReceipt:
    repo.connection.execute(
        """
        INSERT INTO omnix_agent_evidence_receipts (
            workspace_id, run_id, receipt_id, task_revision_id, capability_id,
            source_class, subject, coverage, request_digest, provider, origin,
            source_manifest_id, source_count, executed_at, observed_at,
            freshest_source_at, trust_level, result_digest, metadata
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s::jsonb
        )
        ON CONFLICT (workspace_id, run_id, receipt_id) DO NOTHING
        """,
        (
            repo.context.workspace_id, receipt.run_id, receipt.receipt_id,
            receipt.task_revision_id, receipt.capability_id, receipt.source_class,
            _json(receipt.subject.model_dump(mode="json") if receipt.subject else None),
            _json([item.model_dump(mode="json") for item in receipt.coverage]),
            receipt.request_digest, receipt.provider, receipt.origin,
            receipt.source_manifest_id, receipt.source_count, receipt.executed_at,
            receipt.observed_at, receipt.freshest_source_at, receipt.trust_level,
            receipt.result_digest, _json(receipt.metadata),
        ),
    )
    repo.append_event(AgentEvent(
        run_id=receipt.run_id,
        event_type="evidence.receipt",
        payload={
            "receipt_id": receipt.receipt_id,
            "task_revision_id": receipt.task_revision_id,
            "capability_id": receipt.capability_id,
            "source_class": receipt.source_class,
            "subject": receipt.subject.model_dump(mode="json") if receipt.subject else None,
            "coverage": [item.model_dump(mode="json") for item in receipt.coverage],
            "provider": receipt.provider,
            "observed_at": receipt.observed_at.isoformat(),
            "trust_level": receipt.trust_level,
        },
    ))
    return receipt


def list_evidence_receipts(repo: PostgresAgentRunRepository, run_id: str) -> list[EvidenceReceipt]:
    rows = repo.connection.execute(
        """
        SELECT receipt_id, task_revision_id, capability_id, source_class,
               subject, coverage, request_digest, provider, origin, source_manifest_id,
               source_count, executed_at, observed_at, freshest_source_at,
               trust_level, result_digest, metadata
          FROM omnix_agent_evidence_receipts
         WHERE workspace_id = %s AND run_id = %s
         ORDER BY observed_at, receipt_id
        """,
        (repo.context.workspace_id, run_id),
    ).fetchall()
    return [
        EvidenceReceipt(
            receipt_id=str(row[0]),
            run_id=run_id,
            task_revision_id=str(row[1]) if row[1] else None,
            capability_id=str(row[2]),
            source_class=str(row[3]),
            subject=row[4],
            coverage=list(row[5] or []),
            request_digest=str(row[6]),
            provider=str(row[7]) if row[7] else None,
            origin=str(row[8]) if row[8] else None,
            source_manifest_id=str(row[9]) if row[9] else None,
            source_count=int(row[10] or 0),
            executed_at=row[11],
            observed_at=row[12],
            freshest_source_at=row[13],
            trust_level=cast(Any, str(row[14])),
            result_digest=str(row[15]),
            metadata=dict(row[16] or {}),
        )
        for row in rows
    ]


def mark_superseded(repo: PostgresAgentRunRepository, run_id: str, superseded_by_run_id: str) -> AgentRunSnapshot:
    row = repo.connection.execute(
        """
        UPDATE omnix_agent_runs
           SET superseded_by_run_id = %s,
               status = 'cancelled',
               desired_state = 'cancelled',
               completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
               last_error = %s,
               revision = revision + 1,
               updated_at = CURRENT_TIMESTAMP
         WHERE workspace_id = %s AND run_id = %s
           AND status NOT IN ('completed','failed','cancelled')
        RETURNING revision
        """,
        (
            superseded_by_run_id,
            f"superseded_by:{superseded_by_run_id}",
            repo.context.workspace_id,
            run_id,
        ),
    ).fetchone()
    current = repo.get_run(run_id)
    if current is None:
        raise KeyError(run_id)
    if row is not None:
        repo.append_event(AgentEvent(
            run_id=run_id,
            event_type="run.superseded",
            payload={"superseded_by_run_id": superseded_by_run_id},
        ))
        current = repo.get_run(run_id) or current
    return current
