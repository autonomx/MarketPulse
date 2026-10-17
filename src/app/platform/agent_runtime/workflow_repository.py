"""The workflow tables: definitions, runs, step runs, events and schedules (WP-8.2).

Every statement the workflow runtime issues lives here, scoped to the
caller's workspace. Methods return the cursor; the runtime owns the
transaction and decides what each row means.
"""
from __future__ import annotations

from typing import Any


class PostgresWorkflowRepository:
    """Workflow persistence on one connection, scoped to one workspace."""

    def __init__(self, connection: Any, context: Any) -> None:
        self.connection = connection
        self.context = context

    def lock_run_revision(self, run_id) -> Any:
        return self.connection.execute(
            """
            SELECT revision
              FROM omnix_workflow_runs
             WHERE workspace_id = %s AND run_id = %s
             FOR UPDATE
            """,
            (self.context.workspace_id, run_id),
        )

    def next_event_sequence(self, run_id) -> Any:
        return self.connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1
              FROM omnix_workflow_run_events
             WHERE workspace_id = %s AND run_id = %s
            """,
            (self.context.workspace_id, run_id),
        )

    def insert_event(self, run_id, sequence, event_id, event_type, payload, created_at) -> Any:
        return self.connection.execute(
            """
            INSERT INTO omnix_workflow_run_events (
                workspace_id, run_id, sequence, event_id,
                event_type, payload, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (self.context.workspace_id, run_id, sequence, event_id, event_type, payload, created_at),
        )

    def insert_definition(self, workflow_id, version, name, definition) -> Any:
        return self.connection.execute(
            """
            INSERT INTO omnix_workflow_definitions (
                workspace_id, workflow_id, version, name, definition, active
            ) VALUES (%s, %s, %s, %s, %s::jsonb, TRUE)
            ON CONFLICT (workspace_id, workflow_id, version) DO NOTHING
            RETURNING workflow_id
            """,
            (self.context.workspace_id, workflow_id, version, name, definition),
        )

    def definition_by_version(self, workflow_id, version) -> Any:
        return self.connection.execute(
            """
            SELECT definition
              FROM omnix_workflow_definitions
             WHERE workspace_id = %s AND workflow_id = %s AND version = %s
            """,
            (self.context.workspace_id, workflow_id, version),
        )

    def active_definitions(self) -> Any:
        return self.connection.execute(
            """
            SELECT DISTINCT ON (workflow_id) definition
              FROM omnix_workflow_definitions
             WHERE workspace_id = %s AND active
             ORDER BY workflow_id, version DESC
            """,
            (self.context.workspace_id,),
        )

    def lookup_workflow_id(self, workflow_id, name, name_pattern) -> Any:
        return self.connection.execute(
            """
            SELECT workflow_id
              FROM omnix_workflow_definitions
             WHERE workspace_id = %s AND active
               AND (lower(workflow_id) = %s OR lower(name) = %s OR lower(name) LIKE %s)
             ORDER BY version DESC
             LIMIT 1
            """,
            (self.context.workspace_id, workflow_id, name, name_pattern),
        )

    def insert_run(self, run_id, workflow_id, workflow_version, input_payload, status, current_step_id, idempotency_key) -> Any:
        return self.connection.execute(
            """
            INSERT INTO omnix_workflow_runs (
                workspace_id, run_id, workflow_id, workflow_version,
                input_payload, status, current_step_id, idempotency_key
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            ON CONFLICT (workspace_id, idempotency_key) DO NOTHING
            RETURNING run_id
            """,
            (self.context.workspace_id, run_id, workflow_id, workflow_version, input_payload, status, current_step_id, idempotency_key),
        )

    def run_by_idempotency_key(self, idempotency_key) -> Any:
        return self.connection.execute(
            """
            SELECT run_id, workflow_id, workflow_version
              FROM omnix_workflow_runs
             WHERE workspace_id = %s AND idempotency_key = %s
            """,
            (self.context.workspace_id, idempotency_key),
        )

    def insert_step_run(self, run_id, step_id, ordinal) -> Any:
        return self.connection.execute(
            """
            INSERT INTO omnix_workflow_step_runs (
                workspace_id, run_id, step_id, ordinal, status
            ) VALUES (%s, %s, %s, %s, 'pending')
            """,
            (self.context.workspace_id, run_id, step_id, ordinal),
        )

    def events_after(self, run_id, after_sequence) -> Any:
        return self.connection.execute(
            """
            SELECT event_id, sequence, event_type, payload, created_at
              FROM omnix_workflow_run_events
             WHERE workspace_id = %s AND run_id = %s
               AND sequence > %s
             ORDER BY sequence
             LIMIT 5000
            """,
            (self.context.workspace_id, run_id, after_sequence),
        )

    def recent_run_ids(self, limit) -> Any:
        return self.connection.execute(
            """
            SELECT run_id
              FROM omnix_workflow_runs
             WHERE workspace_id = %s
             ORDER BY created_at DESC, run_id DESC
             LIMIT %s
            """,
            (self.context.workspace_id, limit),
        )

    def recent_run_ids_for_workflow(self, workflow_id, limit) -> Any:
        return self.connection.execute(
            """
            SELECT run_id
              FROM omnix_workflow_runs
             WHERE workspace_id = %s AND workflow_id = %s
             ORDER BY created_at DESC, run_id DESC
             LIMIT %s
            """,
            (self.context.workspace_id, workflow_id, limit),
        )

    def run_status_row(self, run_id) -> Any:
        return self.connection.execute(
            """
            SELECT workflow_id, workflow_version, status, current_step_id,
                   input_payload, revision, last_error,
                   created_at, updated_at, completed_at
              FROM omnix_workflow_runs
             WHERE workspace_id = %s AND run_id = %s
            """,
            (self.context.workspace_id, run_id),
        )

    def latest_active_definition(self, workflow_id) -> Any:
        return self.connection.execute(
            """
            SELECT definition FROM omnix_workflow_definitions
             WHERE workspace_id = %s AND workflow_id = %s AND active
             ORDER BY version DESC LIMIT 1
            """,
            (self.context.workspace_id, workflow_id),
        )

    def insert_schedule(self, schedule_id, workflow_id, workflow_version, input_payload, interval_seconds, next_run_at) -> Any:
        return self.connection.execute(
            """
            INSERT INTO omnix_workflow_schedules (
                workspace_id, schedule_id, workflow_id, workflow_version,
                input_payload, interval_seconds, next_run_at, enabled
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, TRUE)
            ON CONFLICT (workspace_id, schedule_id) DO NOTHING
            RETURNING schedule_id
            """,
            (self.context.workspace_id, schedule_id, workflow_id, workflow_version, input_payload, interval_seconds, next_run_at),
        )

    def schedule_row(self, schedule_id) -> Any:
        return self.connection.execute(
            """
            SELECT workflow_id, workflow_version, input_payload,
                   interval_seconds, next_run_at, enabled
              FROM omnix_workflow_schedules
             WHERE workspace_id = %s AND schedule_id = %s
            """,
            (self.context.workspace_id, schedule_id),
        )

    def schedules(self) -> Any:
        return self.connection.execute(
            """
            SELECT schedule_id, workflow_id, workflow_version, input_payload,
                   interval_seconds, next_run_at, enabled, last_enqueued_at
              FROM omnix_workflow_schedules
             WHERE workspace_id = %s
             ORDER BY created_at, schedule_id
            """,
            (self.context.workspace_id,),
        )

    def disable_schedule(self, schedule_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_schedules
               SET enabled = FALSE, next_run_at = NULL,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND schedule_id = %s
            RETURNING schedule_id
            """,
            (self.context.workspace_id, schedule_id),
        )

    def cancel_pending_fires(self, schedule_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_schedule_fires
               SET status = 'cancelled',
                   last_error = 'schedule_cancelled_before_dispatch',
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND schedule_id = %s
               AND status = 'pending'
            """,
            (self.context.workspace_id, schedule_id),
        )

    def renew_step_leases(self, worker_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_step_runs
               SET lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '90 seconds',
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND worker_id = %s
               AND status = 'running'
            """,
            (self.context.workspace_id, worker_id),
        )

    def expired_running_steps(self) -> Any:
        return self.connection.execute(
            """
            SELECT step.run_id, step.step_id
              FROM omnix_workflow_step_runs AS step
              JOIN omnix_workflow_runs AS run
                ON run.workspace_id = step.workspace_id
               AND run.run_id = step.run_id
             WHERE step.workspace_id = %s
               AND run.status = 'running'
               AND run.current_step_id = step.step_id
               AND step.status = 'running'
               AND step.lease_expires_at <= CURRENT_TIMESTAMP
             FOR UPDATE OF step SKIP LOCKED
            """,
            (self.context.workspace_id,),
        )

    def fail_expired_step(self, error, run_id, step_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_step_runs
               SET status = 'failed', last_error = %s,
                   completed_at = CURRENT_TIMESTAMP,
                   worker_id = NULL, lease_expires_at = NULL,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s AND step_id = %s
               AND status = 'running'
               AND lease_expires_at <= CURRENT_TIMESTAMP
            RETURNING step_id
            """,
            (error, self.context.workspace_id, run_id, step_id),
        )

    def fail_run_for_expired_step(self, error, run_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_runs
               SET status = 'failed', last_error = %s,
                   revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP,
                   completed_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s
               AND status = 'running'
            RETURNING run_id
            """,
            (error, self.context.workspace_id, run_id),
        )

    def runs_to_advance(self) -> Any:
        return self.connection.execute(
            """
            SELECT DISTINCT run.run_id
              FROM omnix_workflow_runs AS run
              JOIN omnix_workflow_step_runs AS step
                ON step.workspace_id = run.workspace_id
               AND step.run_id = run.run_id
               AND step.step_id = run.current_step_id
             WHERE run.workspace_id = %s
               AND run.status = 'running'
               AND step.status IN ('pending','approved','completed')
             ORDER BY run.run_id
            """,
            (self.context.workspace_id,),
        )

    def due_schedules(self) -> Any:
        return self.connection.execute(
            """
            SELECT schedule_id, next_run_at, interval_seconds
              FROM omnix_workflow_schedules
             WHERE workspace_id = %s AND enabled
               AND next_run_at IS NOT NULL
               AND next_run_at <= CURRENT_TIMESTAMP
             ORDER BY next_run_at, schedule_id
             FOR UPDATE SKIP LOCKED
             LIMIT 50
            """,
            (self.context.workspace_id,),
        )

    def insert_schedule_fire(self, schedule_id, scheduled_for) -> Any:
        return self.connection.execute(
            """
            INSERT INTO omnix_workflow_schedule_fires (
                workspace_id, schedule_id, scheduled_for, status
            ) VALUES (%s, %s, %s, 'pending')
            ON CONFLICT (workspace_id, schedule_id, scheduled_for)
            DO NOTHING
            """,
            (self.context.workspace_id, schedule_id, scheduled_for),
        )

    def advance_schedule(self, enqueued_at, next_run_at, enabled, schedule_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_schedules
               SET last_enqueued_at = %s, next_run_at = %s,
                   enabled = %s, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND schedule_id = %s
            """,
            (enqueued_at, next_run_at, enabled, self.context.workspace_id, schedule_id),
        )

    def pending_schedule_fires(self) -> Any:
        return self.connection.execute(
            """
            SELECT fire.schedule_id, fire.scheduled_for,
                   schedule.workflow_id, schedule.workflow_version,
                   schedule.input_payload
              FROM omnix_workflow_schedule_fires AS fire
              JOIN omnix_workflow_schedules AS schedule
                ON schedule.workspace_id = fire.workspace_id
               AND schedule.schedule_id = fire.schedule_id
             WHERE fire.workspace_id = %s AND fire.status = 'pending'
             ORDER BY fire.created_at, fire.schedule_id, fire.scheduled_for
             LIMIT 50
            """,
            (self.context.workspace_id,),
        )

    def fail_schedule_fire(self, schedule_id, scheduled_for) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_schedule_fires
               SET status = 'failed',
                   last_error = 'workflow_definition_missing',
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND schedule_id = %s
               AND scheduled_for = %s AND status = 'pending'
            """,
            (self.context.workspace_id, schedule_id, scheduled_for),
        )

    def start_schedule_fire(self, run_id, schedule_id, scheduled_for) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_schedule_fires
               SET status = 'started', run_id = %s, last_error = NULL,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND schedule_id = %s
               AND scheduled_for = %s AND status = 'pending'
            """,
            (run_id, self.context.workspace_id, schedule_id, scheduled_for),
        )

    def step_state_row(self, run_id, step_id) -> Any:
        return self.connection.execute(
            """
            SELECT status, attempts, result, worker_id, lease_expires_at
              FROM omnix_workflow_step_runs
             WHERE workspace_id = %s AND run_id = %s AND step_id = %s
            """,
            (self.context.workspace_id, run_id, step_id),
        )

    def claim_step(self, worker_id, run_id, step_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_step_runs
               SET status = 'running', attempts = attempts + 1,
                   started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                   last_error = NULL, worker_id = %s,
                   lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '90 seconds',
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s AND step_id = %s
               AND status IN ('pending','approved')
            RETURNING attempts
            """,
            (worker_id, self.context.workspace_id, run_id, step_id),
        )

    def mark_step_waiting_for_approval(self, run_id, step_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_step_runs
               SET status = 'waiting_for_approval'
             WHERE workspace_id = %s AND run_id = %s AND step_id = %s
               AND status = 'pending'
            RETURNING step_id
            """,
            (self.context.workspace_id, run_id, step_id),
        )

    def mark_run_waiting_for_approval(self, run_id, step_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_runs
               SET status = 'waiting_for_approval', revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s
               AND current_step_id = %s
            """,
            (self.context.workspace_id, run_id, step_id),
        )

    def reset_step_for_retry(self, error, run_id, step_id, worker_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_step_runs
               SET status = 'pending', last_error = %s, worker_id = NULL,
                   lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s AND step_id = %s
               AND status = 'running' AND worker_id = %s
            RETURNING step_id
            """,
            (error, self.context.workspace_id, run_id, step_id, worker_id),
        )

    def fail_step(self, error, run_id, step_id, worker_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_step_runs
               SET status = 'failed', last_error = %s,
                   completed_at = CURRENT_TIMESTAMP, worker_id = NULL,
                   lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s AND step_id = %s
               AND status = 'running' AND worker_id = %s
            RETURNING step_id
            """,
            (error, self.context.workspace_id, run_id, step_id, worker_id),
        )

    def complete_step(self, result, run_id, step_id, worker_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_step_runs
               SET status = 'completed', result = %s::jsonb, last_error = NULL,
                   completed_at = CURRENT_TIMESTAMP, worker_id = NULL,
                   lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s AND step_id = %s
               AND status = 'running' AND worker_id = %s
            RETURNING step_id
            """,
            (result, self.context.workspace_id, run_id, step_id, worker_id),
        )

    def move_run_to_step(self, step_id, run_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_runs
               SET current_step_id = %s, status = 'running',
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s
               AND status = 'running'
            RETURNING run_id
            """,
            (step_id, self.context.workspace_id, run_id),
        )

    def skip_unfinished_steps(self, run_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_step_runs
               SET status = 'skipped', completed_at = CURRENT_TIMESTAMP,
                   worker_id = NULL, lease_expires_at = NULL,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s AND status IN ('pending','approved')
            """,
            (self.context.workspace_id, run_id),
        )

    def complete_run(self, run_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_runs
               SET current_step_id = NULL, status = 'completed',
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP,
                   completed_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s
               AND status NOT IN ('failed','cancelled','completed')
            RETURNING run_id
            """,
            (self.context.workspace_id, run_id),
        )

    def set_run_status(self, status, error, terminal, run_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_runs
               SET status = %s, last_error = %s, revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP,
                   completed_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE completed_at END
             WHERE workspace_id = %s AND run_id = %s
            RETURNING run_id
            """,
            (status, error, terminal, self.context.workspace_id, run_id),
        )

    def approve_step(self, approved_by, run_id, step_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_step_runs
               SET status = 'approved', result = jsonb_build_object('approved_by', %s::text)
             WHERE workspace_id = %s AND run_id = %s AND step_id = %s
               AND status = 'waiting_for_approval'
            RETURNING step_id
            """,
            (approved_by, self.context.workspace_id, run_id, step_id),
        )

    def resume_run_after_approval(self, run_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_runs
               SET status = 'running', revision = revision + 1, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s
            """,
            (self.context.workspace_id, run_id),
        )

    def reject_step(self, run_id, step_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_step_runs
               SET status = 'failed', last_error = 'approval_rejected',
                   completed_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s AND step_id = %s
               AND status = 'waiting_for_approval'
            RETURNING step_id
            """,
            (self.context.workspace_id, run_id, step_id),
        )

    def cancel_run_after_rejection(self, run_id) -> Any:
        return self.connection.execute(
            """
            UPDATE omnix_workflow_runs
               SET status = 'cancelled', last_error = 'approval_rejected',
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP,
                   completed_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND run_id = %s
            """,
            (self.context.workspace_id, run_id),
        )

    def step_results(self, run_id) -> Any:
        return self.connection.execute(
            """
            SELECT step_id, result
              FROM omnix_workflow_step_runs
             WHERE workspace_id = %s AND run_id = %s AND result IS NOT NULL
             ORDER BY ordinal
            """,
            (self.context.workspace_id, run_id),
        )
