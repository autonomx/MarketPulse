-- Durable deterministic WorkflowRuntime.
CREATE TABLE IF NOT EXISTS omnix_workflow_definitions (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    workflow_id TEXT NOT NULL,
    version BIGINT NOT NULL CHECK (version >= 1),
    name TEXT NOT NULL,
    definition JSONB NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, workflow_id, version)
);

CREATE INDEX IF NOT EXISTS idx_omnix_workflow_definitions_active
    ON omnix_workflow_definitions (workspace_id, workflow_id, active, version DESC);

CREATE TABLE IF NOT EXISTS omnix_workflow_runs (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_version BIGINT NOT NULL,
    input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued',
    current_step_id TEXT,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    idempotency_key TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, run_id),
    UNIQUE (workspace_id, idempotency_key),
    FOREIGN KEY (workspace_id, workflow_id, workflow_version)
        REFERENCES omnix_workflow_definitions(workspace_id, workflow_id, version)
);

CREATE TABLE IF NOT EXISTS omnix_workflow_step_runs (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    result JSONB,
    last_error TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, run_id, step_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_workflow_runs(workspace_id, run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_workflow_steps_run
    ON omnix_workflow_step_runs (workspace_id, run_id, ordinal);
