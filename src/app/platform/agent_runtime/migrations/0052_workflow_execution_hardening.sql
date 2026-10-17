-- Workflow execution durability hardening.
-- Running steps are leased so a crashed worker becomes an explicit
-- unknown-outcome failure rather than a permanently stuck or blindly retried action.

ALTER TABLE omnix_workflow_step_runs
    ADD COLUMN IF NOT EXISTS worker_id TEXT,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_omnix_workflow_steps_lease
    ON omnix_workflow_step_runs (workspace_id, status, lease_expires_at)
    WHERE status = 'running';
