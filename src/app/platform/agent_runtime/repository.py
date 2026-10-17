"""PostgreSQL repository for generalized agent runs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from collections.abc import Iterable, Iterator
from typing import Any, cast

from app.persistence.outbox_repository import PostgresOutboxRepository
from app.persistence.tenant import TenantContext

from .contracts import (
    AgentApproval,
    AgentArtifact,
    AgentEvent,
    AgentRunCommand,
    AgentRunSnapshot,
    AgentRunSpec,
    AgentRunUsage,
    EvidenceDecision,
    EvidenceReceipt,
    TaskRevision,
    WorkerLease,
)

# Events read per page when walking a whole run (WP-7.4).
EVENT_PAGE_SIZE = 1000


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


class AgentRunConcurrencyError(RuntimeError):
    pass


class AgentLeaseConflict(RuntimeError):
    pass


class PostgresAgentRunRepository:
    def __init__(
        self,
        connection: Any,
        context: TenantContext,
        *,
        lease_token_provider=None,
    ) -> None:
        self.connection = connection
        self.context = context
        self.lease_token_provider = lease_token_provider
        self.outbox = PostgresOutboxRepository(connection)

    def create_run(self, spec: AgentRunSpec) -> AgentRunSnapshot:
        row = self.connection.execute(
            """
            INSERT INTO omnix_agent_runs (
                workspace_id, run_id, session_id, parent_run_id, supersedes_run_id, spec,
                status, desired_state
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'queued', 'running')
            ON CONFLICT (workspace_id, run_id) DO NOTHING
            RETURNING run_id
            """,
            (
                self.context.workspace_id,
                spec.run_id,
                spec.session_id,
                spec.parent_run_id,
                spec.supersedes_run_id,
                _json(spec),
            ),
        ).fetchone()
        if row is None:
            existing = self.get_run(spec.run_id)
            if existing is None:
                raise AgentRunConcurrencyError("agent run create conflict")
            if existing.spec != spec:
                raise AgentRunConcurrencyError("run_id already exists with a different spec")
            return existing
        snapshot = self.get_run(spec.run_id)
        assert snapshot is not None
        self.append_event(AgentEvent(run_id=spec.run_id, event_type="run.created", payload={"profile": spec.profile, "runtime": spec.runtime}))
        self.add_task_revision(TaskRevision(
            run_id=spec.run_id,
            sequence=1,
            user_instruction=spec.task,
            effective_objective=spec.objective or spec.task,
            effective_success_criteria=list(spec.success_criteria),
            evidence_decision=EvidenceDecision(
                policy=spec.evidence_policy,
                confidence=1.0,
                reason="compiled_run_spec",
                classifier="deterministic",
            ),
            required_local_capabilities=list(spec.capabilities),
            required_external_capabilities=list(spec.external_capabilities),
            expected_artifacts=list(spec.expected_artifacts),
        ))
        return snapshot

    def get_run(self, run_id: str) -> AgentRunSnapshot | None:
        row = self.connection.execute(
            """
            SELECT omnix_agent_runs.run_id,
                   omnix_agent_runs.spec,
                   omnix_agent_runs.status,
                   omnix_agent_runs.desired_state,
                   omnix_agent_runs.revision,
                   omnix_agent_runs.worker_id,
                   omnix_agent_runs.superseded_by_run_id,
                   run_usage.input_tokens,
                   run_usage.output_tokens,
                   run_usage.input_tokens_reported,
                   run_usage.output_tokens_reported,
                   omnix_agent_runs.started_at,
                   omnix_agent_runs.completed_at,
                   omnix_agent_runs.last_error,
                   omnix_agent_runs.created_at,
                   omnix_agent_runs.updated_at
              FROM omnix_agent_runs
              LEFT JOIN omnix_agent_run_usage AS run_usage
                ON run_usage.workspace_id = omnix_agent_runs.workspace_id
               AND run_usage.run_id = omnix_agent_runs.run_id
             WHERE omnix_agent_runs.workspace_id = %s AND omnix_agent_runs.run_id = %s
            """,
            (self.context.workspace_id, run_id),
        ).fetchone()
        if row is None:
            return None
        return AgentRunSnapshot(
            run_id=str(row[0]),
            spec=AgentRunSpec.model_validate(row[1]),
            status=cast(Any, str(row[2])),
            desired_state=cast(Any, str(row[3])),
            revision=int(row[4]),
            worker_id=str(row[5]) if row[5] else None,
            superseded_by_run_id=str(row[6]) if row[6] else None,
            usage=AgentRunUsage(
                input_tokens=int(row[7] or 0),
                output_tokens=int(row[8] or 0),
                input_tokens_reported=bool(row[9]),
                output_tokens_reported=bool(row[10]),
            ),
            started_at=row[11],
            completed_at=row[12],
            last_error=str(row[13]) if row[13] else None,
            created_at=row[14],
            updated_at=row[15],
        )

    def update_spec(
        self,
        run_id: str,
        *,
        expected_revision: int,
        spec: AgentRunSpec,
    ) -> AgentRunSnapshot:
        """Persist a worker-prepared specification with optimistic fencing."""
        row = self.connection.execute(
            """
            UPDATE omnix_agent_runs
               SET spec = %s::jsonb, revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s AND revision = %s
               AND status = 'queued'
            RETURNING revision
            """,
            (_json(spec), self.context.workspace_id, run_id, expected_revision),
        ).fetchone()
        if row is None:
            raise AgentRunConcurrencyError("agent run spec revision mismatch")
        snapshot = self.get_run(run_id)
        if snapshot is None:
            raise KeyError(run_id)
        self.append_event(
            AgentEvent(
                run_id=run_id,
                event_type="run.status",
                payload={"status": snapshot.status, "workspace_prepared": True},
            )
        )
        return snapshot

    def add_task_revision(self, revision: TaskRevision) -> TaskRevision:
        from . import run_repository_revisions

        return run_repository_revisions.add_task_revision(self, revision)

    def list_task_revisions(self, run_id: str) -> list[TaskRevision]:
        from . import run_repository_revisions

        return run_repository_revisions.list_task_revisions(self, run_id)

    def latest_task_revision(self, run_id: str) -> TaskRevision | None:
        from . import run_repository_revisions

        return run_repository_revisions.latest_task_revision(self, run_id)

    def add_evidence_receipt(self, receipt: EvidenceReceipt) -> EvidenceReceipt:
        from . import run_repository_revisions

        return run_repository_revisions.add_evidence_receipt(self, receipt)

    def list_evidence_receipts(self, run_id: str) -> list[EvidenceReceipt]:
        from . import run_repository_revisions

        return run_repository_revisions.list_evidence_receipts(self, run_id)

    def mark_superseded(self, run_id: str, superseded_by_run_id: str) -> AgentRunSnapshot:
        from . import run_repository_revisions

        return run_repository_revisions.mark_superseded(self, run_id, superseded_by_run_id)

    def list_children(self, parent_run_id: str) -> list[AgentRunSnapshot]:
        rows = self.connection.execute(
            """
            SELECT run_id
              FROM omnix_agent_runs
             WHERE workspace_id = %s AND parent_run_id = %s
             ORDER BY created_at, run_id
            """,
            (self.context.workspace_id, parent_run_id),
        ).fetchall()
        result: list[AgentRunSnapshot] = []
        for row in rows:
            snapshot = self.get_run(str(row[0]))
            if snapshot is not None:
                result.append(snapshot)
        return result

    def update_state(
        self,
        run_id: str,
        *,
        expected_revision: int,
        status: str | None = None,
        desired_state: str | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
        last_error: str | None = None,
    ) -> AgentRunSnapshot:
        if worker_id is not None and lease_token is None and callable(self.lease_token_provider):
            lease_token = self.lease_token_provider(run_id)
        if bool(worker_id) != bool(lease_token):
            raise AgentLeaseConflict("worker state updates require matching lease credentials")
        current = self.get_run(run_id)
        if current is None:
            raise KeyError(run_id)
        next_status = status or current.status
        next_desired = desired_state or current.desired_state
        started = current.started_at
        completed = current.completed_at
        now = datetime.now(timezone.utc)
        if next_status in {"starting", "running"} and started is None:
            started = now
        if next_status in {"completed", "failed", "cancelled"} and completed is None:
            completed = now
        row = self.connection.execute(
            """
            UPDATE omnix_agent_runs
               SET status = %s, desired_state = %s, worker_id = %s,
                   last_error = %s, started_at = %s, completed_at = %s,
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s AND revision = %s
               AND (
                    %s
                    OR EXISTS (
                        SELECT 1
                          FROM omnix_agent_worker_leases AS lease
                         WHERE lease.workspace_id = omnix_agent_runs.workspace_id
                           AND lease.run_id = omnix_agent_runs.run_id
                           AND lease.worker_id = %s
                           AND lease.lease_token = %s
                           AND lease.lease_expires_at > CURRENT_TIMESTAMP
                    )
               )
            RETURNING revision
            """,
            (
                next_status,
                next_desired,
                worker_id if worker_id is not None else current.worker_id,
                last_error,
                started,
                completed,
                self.context.workspace_id,
                run_id,
                expected_revision,
                worker_id is None,
                worker_id,
                lease_token,
            ),
        ).fetchone()
        if row is None:
            # A concurrent writer that won the revision race is not a lease
            # failure; only report the lease when the revision still matched.
            latest = self.connection.execute(
                "SELECT revision FROM omnix_agent_runs WHERE workspace_id = %s AND run_id = %s",
                (self.context.workspace_id, run_id),
            ).fetchone()
            if worker_id is not None and latest is not None and latest[0] == expected_revision:
                raise AgentLeaseConflict("agent run lease token is stale or expired")
            raise AgentRunConcurrencyError("agent run revision mismatch")
        updated = self.get_run(run_id)
        assert updated is not None
        self.append_event(
            AgentEvent(
                run_id=run_id,
                event_type="run.status",
                payload={"status": updated.status, "desired_state": updated.desired_state, "revision": updated.revision},
            )
        )
        return updated

    def append_event(self, event: AgentEvent) -> AgentEvent:
        from . import run_repository_events

        return run_repository_events.append_event(self, event)

    def _append_event(self, event: AgentEvent) -> AgentEvent:
        from . import run_repository_events

        return run_repository_events._append_event(self, event)

    def list_events(self, run_id: str, *, after_sequence: int = 0, limit: int = 500) -> list[AgentEvent]:
        from . import run_repository_events

        return run_repository_events.list_events(self, run_id, after_sequence=after_sequence, limit=limit)

    def iter_events(
        self,
        run_id: str,
        *,
        event_types: Iterable[str] | None = None,
        page_size: int = EVENT_PAGE_SIZE,
    ) -> Iterator[AgentEvent]:
        from . import run_repository_events

        return run_repository_events.iter_events(self, run_id, event_types=event_types, page_size=page_size)

    def latest_event(
        self,
        run_id: str,
        event_type: str,
        *,
        payload_contains: dict[str, Any] | None = None,
    ) -> AgentEvent | None:
        from . import run_repository_events

        return run_repository_events.latest_event(self, run_id, event_type, payload_contains=payload_contains)

    def _event_page(
        self,
        run_id: str,
        *,
        after_sequence: int,
        limit: int,
        event_types: list[str] | None = None,
    ) -> list[AgentEvent]:
        from . import run_repository_events

        return run_repository_events._event_page(self, run_id, after_sequence=after_sequence, limit=limit, event_types=event_types)

    @staticmethod
    def _events_from_rows(run_id: str, rows: list[Any]) -> list[AgentEvent]:
        from . import run_repository_events

        return run_repository_events._events_from_rows(run_id, rows)

    def latest_progress_event(self, run_id: str) -> AgentEvent | None:
        from . import run_repository_events

        return run_repository_events.latest_progress_event(self, run_id)

    def count_events(self, run_id: str, event_type: str) -> int:
        from . import run_repository_events

        return run_repository_events.count_events(self, run_id, event_type)

    def enqueue_command(self, command: AgentRunCommand) -> AgentRunCommand:
        from . import run_repository_commands

        return run_repository_commands.enqueue_command(self, command)

    def enqueue_command_with_status(self, command: AgentRunCommand) -> tuple[AgentRunCommand, str]:
        from . import run_repository_commands

        return run_repository_commands.enqueue_command_with_status(self, command)

    def claim_command(self, run_id: str, command_id: str) -> bool:
        from . import run_repository_commands

        return run_repository_commands.claim_command(self, run_id, command_id)

    def complete_command(self, run_id: str, command_id: str) -> None:
        from . import run_repository_commands

        return run_repository_commands.complete_command(self, run_id, command_id)

    def reset_processing_commands(self, run_id: str) -> None:
        from . import run_repository_commands

        return run_repository_commands.reset_processing_commands(self, run_id)

    def claim_commands(self, run_id: str, *, limit: int = 20) -> list[AgentRunCommand]:
        from . import run_repository_commands

        return run_repository_commands.claim_commands(self, run_id, limit=limit)

    def list_pending_commands(self, run_id: str, *, limit: int = 100) -> list[AgentRunCommand]:
        from . import run_repository_commands

        return run_repository_commands.list_pending_commands(self, run_id, limit=limit)

    def add_approval(self, approval: AgentApproval) -> AgentApproval:
        from . import run_repository_approvals

        return run_repository_approvals.add_approval(self, approval)

    def workspace_approval(
        self, run_id: str, capability_id: str, request_payload: dict[str, Any],
    ) -> AgentApproval:
        from . import run_repository_approvals

        return run_repository_approvals.workspace_approval(self, run_id, capability_id, request_payload)

    def get_approval(self, run_id: str, approval_id: str) -> AgentApproval | None:
        from . import run_repository_approvals

        return run_repository_approvals.get_approval(self, run_id, approval_id)

    def list_approvals(
        self,
        run_id: str,
        *,
        state: str | None = None,
    ) -> list[AgentApproval]:
        from . import run_repository_approvals

        return run_repository_approvals.list_approvals(self, run_id, state=state)

    def resolve_approval(
        self, run_id: str, approval_id: str, *, approved: bool,
        resolution_payload: dict[str, Any] | None = None,
    ) -> AgentApproval:
        from . import run_repository_approvals

        return run_repository_approvals.resolve_approval(self, run_id, approval_id, approved=approved, resolution_payload=resolution_payload)

    def add_artifact(self, artifact: AgentArtifact) -> AgentArtifact:
        from . import run_repository_approvals

        return run_repository_approvals.add_artifact(self, artifact)

    def list_artifacts(self, run_id: str) -> list[AgentArtifact]:
        from . import run_repository_approvals

        return run_repository_approvals.list_artifacts(self, run_id)

    def ensure_capability_execution(
        self,
        run_id: str,
        execution_key: str,
        capability_id: str,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        from . import run_repository_capabilities

        return run_repository_capabilities.ensure_capability_execution(self, run_id, execution_key, capability_id, request_payload)

    def find_capability_approval(
        self,
        run_id: str,
        capability_id: str,
        execution_key: str,
    ) -> AgentApproval | None:
        from . import run_repository_capabilities

        return run_repository_capabilities.find_capability_approval(self, run_id, capability_id, execution_key)

    def mark_capability_waiting_for_approval(
        self,
        run_id: str,
        execution_key: str,
    ) -> None:
        from . import run_repository_capabilities

        return run_repository_capabilities.mark_capability_waiting_for_approval(self, run_id, execution_key)

    def claim_capability_execution(self, run_id: str, execution_key: str) -> bool:
        from . import run_repository_capabilities

        return run_repository_capabilities.claim_capability_execution(self, run_id, execution_key)

    def finish_capability_execution(
        self,
        run_id: str,
        execution_key: str,
        *,
        result_payload: dict[str, Any],
        error: str | None,
        state_changed: bool,
    ) -> None:
        from . import run_repository_capabilities

        return run_repository_capabilities.finish_capability_execution(self, run_id, execution_key, result_payload=result_payload, error=error, state_changed=state_changed)

    def reserve_evidence_query(
        self,
        run_id: str,
        task_revision_id: str,
        execution_key: str,
        *,
        max_queries: int,
        max_sources: int,
        max_extracts: int,
        requested_sources: int,
        requested_extracts: int,
    ) -> dict[str, Any]:
        from . import run_repository_capabilities

        return run_repository_capabilities.reserve_evidence_query(self, run_id, task_revision_id, execution_key, max_queries=max_queries, max_sources=max_sources, max_extracts=max_extracts, requested_sources=requested_sources, requested_extracts=requested_extracts)

    def finish_evidence_query(
        self,
        run_id: str,
        task_revision_id: str,
        execution_key: str,
        *,
        actual_sources: int,
        actual_extracts: int,
        failed: bool,
    ) -> None:
        from . import run_repository_capabilities

        return run_repository_capabilities.finish_evidence_query(self, run_id, task_revision_id, execution_key, actual_sources=actual_sources, actual_extracts=actual_extracts, failed=failed)

    def reclaim_stale_read_capability_execution(
        self,
        run_id: str,
        execution_key: str,
        *,
        stale_before: datetime,
    ) -> bool:
        from . import run_repository_capabilities

        return run_repository_capabilities.reclaim_stale_read_capability_execution(self, run_id, execution_key, stale_before=stale_before)

    def get_usage(self, run_id: str) -> dict[str, Any]:
        from . import run_repository_usage

        return run_repository_usage.get_usage(self, run_id)

    def consume_usage(
        self,
        run_id: str,
        *,
        steps: int = 0,
        tool_calls: int = 0,
        model_calls: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        input_tokens_reported: bool = False,
        output_tokens_reported: bool = False,
        cost: float = 0.0,
        max_steps: int | None = None,
        max_tool_calls: int | None = None,
        max_output_tokens: int | None = None,
        max_cost: float | None = None,
    ) -> dict[str, Any] | None:
        from . import run_repository_usage

        return run_repository_usage.consume_usage(self, run_id, steps=steps, tool_calls=tool_calls, model_calls=model_calls, input_tokens=input_tokens, output_tokens=output_tokens, input_tokens_reported=input_tokens_reported, output_tokens_reported=output_tokens_reported, cost=cost, max_steps=max_steps, max_tool_calls=max_tool_calls, max_output_tokens=max_output_tokens, max_cost=max_cost)

    def acquire_lease(self, run_id: str, *, worker_id: str, ttl_seconds: int = 30) -> WorkerLease:
        from . import run_repository_usage

        return run_repository_usage.acquire_lease(self, run_id, worker_id=worker_id, ttl_seconds=ttl_seconds)

    def get_active_lease(self, run_id: str) -> WorkerLease | None:
        from . import run_repository_usage

        return run_repository_usage.get_active_lease(self, run_id)


    def renew_lease(self, run_id: str, *, worker_id: str, ttl_seconds: int = 30) -> WorkerLease:
        from . import run_repository_usage

        return run_repository_usage.renew_lease(self, run_id, worker_id=worker_id, ttl_seconds=ttl_seconds)
