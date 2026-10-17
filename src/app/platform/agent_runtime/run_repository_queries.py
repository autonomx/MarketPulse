"""Agent run queries the run service, supervisor, budgets and quality pipeline issue (WP-8.2).

Row locks, recovery scans, supersession and task-revision contract updates.
Methods return the cursor; callers own the transaction. This class is built
directly on the transaction's connection, so it is independent of the
repository factory a run service may be given.
"""
from __future__ import annotations

from typing import Any


class PostgresAgentRunQueries:
    """Agent run queries on one connection, scoped to one workspace."""

    def __init__(self, connection: Any, context: Any) -> None:
        self.connection = connection
        self.context = context

    def lock_run(self, run_id) -> Any:
        return self.connection.execute(
            """
            SELECT run_id
              FROM omnix_agent_runs
             WHERE workspace_id = %s AND run_id = %s
             FOR UPDATE
            """,
            (self.context.workspace_id, run_id),
        )

    def quality_stage(self, run_id) -> Any:
        return self.connection.execute(
            """
            SELECT stage, attempt
              FROM omnix_agent_coding_quality_state
             WHERE workspace_id = %s AND run_id = %s
            """,
            (self.context.workspace_id, run_id),
        )

    def owned_active_run_ids(self, worker_id) -> Any:
        return self.connection.execute(
            """
            SELECT run_id
              FROM omnix_agent_runs
             WHERE workspace_id = %s AND worker_id = %s
               AND status NOT IN ('completed','failed','cancelled')
             ORDER BY created_at, run_id
            """,
            (self.context.workspace_id, worker_id),
        )

    def lock_run_skip_locked(self, run_id) -> Any:
        return self.connection.execute(
            """
            SELECT run_id
              FROM omnix_agent_runs
             WHERE workspace_id = %s AND run_id = %s
             FOR UPDATE SKIP LOCKED
            """,
            (self.context.workspace_id, run_id),
        )

    def lock_superseding_run_id(self, run_id) -> Any:
        return self.connection.execute(
            """
            SELECT superseded_by_run_id
              FROM omnix_agent_runs
             WHERE workspace_id = %s AND run_id = %s
             FOR UPDATE
            """,
            (self.context.workspace_id, run_id),
        )

    def orphaned_run_ids(self) -> Any:
        return self.connection.execute(
            """
            SELECT run.run_id
              FROM omnix_agent_runs AS run
              LEFT JOIN omnix_agent_worker_leases AS lease
                ON lease.workspace_id = run.workspace_id AND lease.run_id = run.run_id
             WHERE run.workspace_id = %s
               AND run.status IN ('starting','running','resume_requested')
               AND run.desired_state = 'running'
               AND (lease.run_id IS NULL OR lease.lease_expires_at <= CURRENT_TIMESTAMP)
             ORDER BY run.created_at
            """,
            (self.context.workspace_id,),
        )

    def owned_unfinished_run_ids(self, worker_id) -> Any:
        return self.connection.execute(
            """
            SELECT run_id
              FROM omnix_agent_runs
             WHERE workspace_id = %s AND worker_id = %s
               AND status NOT IN ('completed','failed','cancelled')
            """,
            (self.context.workspace_id, worker_id),
        )

    def finished_parents_with_unfinished_children(self) -> Any:
        return self.connection.execute(
            """
            SELECT DISTINCT parent.run_id
              FROM omnix_agent_runs AS parent
              JOIN omnix_agent_runs AS child
                ON child.workspace_id = parent.workspace_id
               AND child.parent_run_id = parent.run_id
             WHERE parent.workspace_id = %s
               AND parent.status IN ('completed','failed','cancelled')
               AND child.status NOT IN ('completed','failed','cancelled')
             ORDER BY parent.run_id
            """,
            (self.context.workspace_id,),
        )

    def finished_run_ids(self, run_ids) -> Any:
        return self.connection.execute(
            """
            SELECT run_id
              FROM omnix_agent_runs
             WHERE workspace_id = %s
               AND run_id = ANY(%s)
               AND status IN ('completed','failed','cancelled')
            """,
            (self.context.workspace_id, run_ids),
        )

    def latest_task_revision_id(self, run_id) -> Any:
        return self.connection.execute(
            """
            SELECT revision_id
              FROM omnix_agent_task_revisions
             WHERE workspace_id = %s AND run_id = %s
             ORDER BY sequence DESC
             LIMIT 1
            """,
            (self.context.workspace_id, run_id),
        )

    def retire_stale_quality_resumes(self, run_id, revision_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_agent_run_commands
               SET status = 'consumed', consumed_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s
               AND run_id = %s
               AND status = 'pending'
               AND command_type = 'resume'
               AND payload ? 'quality_stage'
               AND (payload ->> 'task_revision_id') IS DISTINCT FROM %s
            """,
            (self.context.workspace_id, run_id, revision_id),
        )

    def mark_planning_state_stale(self, run_id, revision_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_agent_planning_state
               SET status = 'stale', updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s
               AND run_id = %s
               AND task_revision_id IS DISTINCT FROM %s
            """,
            (self.context.workspace_id, run_id, revision_id),
        )

    def update_task_revision_contract(self, requirements, constraints, validation_plan, run_id, revision_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_agent_task_revisions
               SET requirements = %s::jsonb,
                   constraints = %s::jsonb,
                   validation_plan = %s::jsonb
             WHERE workspace_id = %s AND run_id = %s AND revision_id = %s
            """,
            (requirements, constraints, validation_plan, self.context.workspace_id, run_id, revision_id),
        )

    def task_revision_contract(self, run_id, revision_id) -> Any:
        return self.connection.execute(
            """
            SELECT requirements, constraints, validation_plan
              FROM omnix_agent_task_revisions
             WHERE workspace_id = %s AND run_id = %s AND revision_id = %s
            """,
            (self.context.workspace_id, run_id, revision_id),
        )

    def quality_reconciliation_run_ids(self) -> Any:
        return self.connection.execute(
            """
            SELECT run.run_id
              FROM omnix_agent_runs AS run
              JOIN omnix_agent_coding_quality_state AS quality
                ON quality.workspace_id = run.workspace_id
               AND quality.run_id = run.run_id
             WHERE run.workspace_id = %s
               AND run.desired_state = 'running'
               AND (
                    (run.status = 'waiting_for_children' AND quality.stage = 'reviewing')
                    OR
                    (run.status = 'running' AND quality.stage = 'acceptance')
               )
             ORDER BY run.created_at, run.run_id
            """,
            (self.context.workspace_id,),
        )
