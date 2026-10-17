-- Durable workflow scheduling and run history support.
-- Schedule fires are persisted before dispatch so a worker crash cannot silently
-- lose a due workflow execution. Each fire gets a deterministic run idempotency key.

CREATE TABLE IF NOT EXISTS omnix_workflow_schedules (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    schedule_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_version BIGINT NOT NULL,
    input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    interval_seconds BIGINT CHECK (interval_seconds IS NULL OR interval_seconds >= 60),
    next_run_at TIMESTAMPTZ,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_enqueued_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, schedule_id),
    FOREIGN KEY (workspace_id, workflow_id, workflow_version)
        REFERENCES omnix_workflow_definitions(workspace_id, workflow_id, version)
);

CREATE INDEX IF NOT EXISTS idx_omnix_workflow_schedules_due
    ON omnix_workflow_schedules (workspace_id, next_run_at)
    WHERE enabled AND next_run_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS omnix_workflow_schedule_fires (
    workspace_id TEXT NOT NULL,
    schedule_id TEXT NOT NULL,
    scheduled_for TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    run_id TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, schedule_id, scheduled_for),
    FOREIGN KEY (workspace_id, schedule_id)
        REFERENCES omnix_workflow_schedules(workspace_id, schedule_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_workflow_schedule_fires_pending
    ON omnix_workflow_schedule_fires (workspace_id, status, created_at)
    WHERE status = 'pending';


CREATE TABLE IF NOT EXISTS omnix_workflow_run_events (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    sequence BIGINT NOT NULL CHECK (sequence >= 1),
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, sequence),
    UNIQUE (workspace_id, event_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_workflow_runs(workspace_id, run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_workflow_run_events_created
    ON omnix_workflow_run_events (workspace_id, run_id, created_at, sequence);
