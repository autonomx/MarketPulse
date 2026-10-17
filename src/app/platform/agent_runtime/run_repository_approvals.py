"""Agent run storage: approvals and artifacts (WP-8.2).

Functions over the run repository; ``PostgresAgentRunRepository`` keeps one
delegator per function, so callers and tests are unchanged.
"""
from __future__ import annotations

from typing import Any, cast

from app.capabilities.registry import capability_definition_hash
from .contracts import (
    AgentApproval,
    AgentArtifact,
    AgentEvent,
)
from typing import TYPE_CHECKING
from .repository import (
    AgentRunConcurrencyError,
    _json,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.repository import PostgresAgentRunRepository


def add_approval(repo: PostgresAgentRunRepository, approval: AgentApproval) -> AgentApproval:
    repo.connection.execute(
        """
        INSERT INTO omnix_agent_approvals (
            workspace_id, run_id, approval_id, capability_id, state,
            request_payload, resolution_payload, created_at, resolved_at, capability_definition_hash
        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
        ON CONFLICT (workspace_id, run_id, approval_id) DO NOTHING
        """,
        (
            repo.context.workspace_id,
            approval.run_id,
            approval.approval_id,
            approval.capability_id,
            approval.state,
            _json(approval.request_payload),
            _json(approval.resolution_payload),
            approval.created_at,
            approval.resolved_at,
            approval.capability_definition_hash,
        ),
    )
    repo.append_event(AgentEvent(run_id=approval.run_id, event_type="approval.requested", payload={"approval_id": approval.approval_id, "capability_id": approval.capability_id}))
    return approval


def workspace_approval(
    repo: PostgresAgentRunRepository, run_id: str, capability_id: str, request_payload: dict[str, Any],
) -> AgentApproval:
    """Deduplicate an exact workspace action without deriving its approval ID.

    The run row serializes concurrent proposals. The caller commits both the
    approval and any run state change in the same transaction.
    """
    run = repo.connection.execute(
        """
        SELECT run_id FROM omnix_agent_runs
         WHERE workspace_id = %s AND run_id = %s FOR UPDATE
        """,
        (repo.context.workspace_id, run_id),
    ).fetchone()
    if run is None:
        raise KeyError(run_id)
    row = repo.connection.execute(
        """
        SELECT approval_id FROM omnix_agent_approvals
         WHERE workspace_id = %s AND run_id = %s AND capability_id = %s
           AND request_payload = %s::jsonb
           AND capability_definition_hash IS NOT DISTINCT FROM %s
         ORDER BY created_at DESC, approval_id LIMIT 1
        """,
        (repo.context.workspace_id, run_id, capability_id, _json(request_payload),
         capability_definition_hash(capability_id)),
    ).fetchone()
    if row is not None:
        approval = repo.get_approval(run_id, str(row[0]))
        if approval is None:
            raise AgentRunConcurrencyError("workspace approval disappeared")
        return approval
    return repo.add_approval(AgentApproval(
        run_id=run_id, capability_id=capability_id, request_payload=request_payload,
    ))


def get_approval(repo: PostgresAgentRunRepository, run_id: str, approval_id: str) -> AgentApproval | None:
    row = repo.connection.execute(
        """
        SELECT capability_id, state, request_payload, resolution_payload,
               created_at, resolved_at, capability_definition_hash
          FROM omnix_agent_approvals
         WHERE workspace_id = %s AND run_id = %s AND approval_id = %s
        """,
        (repo.context.workspace_id, run_id, approval_id),
    ).fetchone()
    if row is None:
        return None
    return AgentApproval(
        approval_id=approval_id, run_id=run_id, capability_id=str(row[0]),
        state=cast(Any, str(row[1])), request_payload=dict(row[2] or {}),
        resolution_payload=dict(row[3] or {}), created_at=row[4], resolved_at=row[5],
    ).with_definition_hash(row[6])


def list_approvals(
    repo: PostgresAgentRunRepository,
    run_id: str,
    *,
    state: str | None = None,
) -> list[AgentApproval]:
    if state is None:
        rows = repo.connection.execute(
            """
            SELECT approval_id, capability_id, state, request_payload,
                   resolution_payload, created_at, resolved_at, capability_definition_hash
              FROM omnix_agent_approvals
             WHERE workspace_id = %s AND run_id = %s
             ORDER BY created_at, approval_id
            """,
            (repo.context.workspace_id, run_id),
        ).fetchall()
    else:
        rows = repo.connection.execute(
            """
            SELECT approval_id, capability_id, state, request_payload,
                   resolution_payload, created_at, resolved_at, capability_definition_hash
              FROM omnix_agent_approvals
             WHERE workspace_id = %s AND run_id = %s AND state = %s
             ORDER BY created_at, approval_id
            """,
            (repo.context.workspace_id, run_id, state),
        ).fetchall()
    return [
        AgentApproval(
            approval_id=str(row[0]),
            run_id=run_id,
            capability_id=str(row[1]),
            state=cast(Any, str(row[2])),
            request_payload=dict(row[3] or {}),
            resolution_payload=dict(row[4] or {}),
            created_at=row[5],
            resolved_at=row[6],
        ).with_definition_hash(row[7])
        for row in rows
    ]


def resolve_approval(
    repo: PostgresAgentRunRepository, run_id: str, approval_id: str, *, approved: bool,
    resolution_payload: dict[str, Any] | None = None,
) -> AgentApproval:
    state = "approved" if approved else "rejected"
    row = repo.connection.execute(
        """
        UPDATE omnix_agent_approvals
           SET state = %s, resolution_payload = %s::jsonb, resolved_at = CURRENT_TIMESTAMP
         WHERE workspace_id = %s AND run_id = %s AND approval_id = %s AND state = 'pending'
        RETURNING capability_id, request_payload, resolution_payload, created_at, resolved_at,
                  capability_definition_hash
        """,
        (state, _json(resolution_payload or {}), repo.context.workspace_id, run_id, approval_id),
    ).fetchone()
    if row is None:
        existing = repo.get_approval(run_id, approval_id)
        if existing is None:
            raise KeyError(approval_id)
        return existing
    approval = AgentApproval(
        approval_id=approval_id, run_id=run_id, capability_id=str(row[0]), state=cast(Any, state),
        request_payload=dict(row[1] or {}), resolution_payload=dict(row[2] or {}),
        created_at=row[3], resolved_at=row[4],
    ).with_definition_hash(row[5])
    repo.append_event(AgentEvent(
        run_id=run_id, event_type="approval.resolved",
        payload={"approval_id": approval_id, "state": state, "capability_id": approval.capability_id},
    ))
    return approval


def add_artifact(repo: PostgresAgentRunRepository, artifact: AgentArtifact) -> AgentArtifact:
    repo.connection.execute(
        """
        INSERT INTO omnix_agent_artifacts (
            workspace_id, run_id, artifact_id, kind, name, storage_ref,
            checksum, metadata, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (workspace_id, run_id, artifact_id) DO NOTHING
        """,
        (
            repo.context.workspace_id,
            artifact.run_id,
            artifact.artifact_id,
            artifact.kind,
            artifact.name,
            artifact.storage_ref,
            artifact.checksum,
            _json(artifact.metadata),
            artifact.created_at,
        ),
    )
    repo.append_event(AgentEvent(run_id=artifact.run_id, event_type="artifact.created", payload={"artifact_id": artifact.artifact_id, "kind": artifact.kind, "name": artifact.name}))
    return artifact


def list_artifacts(repo: PostgresAgentRunRepository, run_id: str) -> list[AgentArtifact]:
    rows = repo.connection.execute(
        """
        SELECT artifact_id, kind, name, storage_ref, checksum, metadata, created_at
          FROM omnix_agent_artifacts
         WHERE workspace_id = %s AND run_id = %s
         ORDER BY created_at, artifact_id
        """,
        (repo.context.workspace_id, run_id),
    ).fetchall()
    return [
        AgentArtifact(
            artifact_id=str(row[0]),
            run_id=run_id,
            kind=cast(Any, str(row[1])),
            name=str(row[2]),
            storage_ref=str(row[3]) if row[3] else None,
            checksum=str(row[4]) if row[4] else None,
            metadata=dict(row[5] or {}),
            created_at=row[6],
        )
        for row in rows
    ]
