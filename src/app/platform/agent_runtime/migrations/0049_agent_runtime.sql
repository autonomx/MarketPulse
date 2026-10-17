-- Generalized agent/workflow runtime authority.
-- Structured mutable run state is PostgreSQL-authoritative per ADR-0001.

CREATE TABLE IF NOT EXISTS omnix_agent_runs (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    session_id TEXT,
    parent_run_id TEXT,
    spec JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    desired_state TEXT NOT NULL DEFAULT 'running',
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    worker_id TEXT,
    last_error TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_runs_status
    ON omnix_agent_runs (workspace_id, status, updated_at, run_id);
CREATE INDEX IF NOT EXISTS idx_omnix_agent_runs_session
    ON omnix_agent_runs (workspace_id, session_id, created_at DESC)
    WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_omnix_agent_runs_parent
    ON omnix_agent_runs (workspace_id, parent_run_id, created_at)
    WHERE parent_run_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS omnix_agent_run_events (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    sequence BIGINT NOT NULL CHECK (sequence >= 1),
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    correlation_id TEXT,
    causation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, sequence),
    UNIQUE (workspace_id, event_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_events_created
    ON omnix_agent_run_events (workspace_id, run_id, created_at, sequence);

CREATE TABLE IF NOT EXISTS omnix_agent_run_commands (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    consumed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, run_id, command_id),
    UNIQUE (workspace_id, run_id, idempotency_key),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_commands_pending
    ON omnix_agent_run_commands (workspace_id, run_id, created_at, command_id)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS omnix_agent_approvals (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    resolution_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, run_id, approval_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_approvals_pending
    ON omnix_agent_approvals (workspace_id, run_id, created_at)
    WHERE state = 'pending';

CREATE TABLE IF NOT EXISTS omnix_agent_artifacts (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    storage_ref TEXT,
    checksum TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, artifact_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_agent_worker_leases (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    PRIMARY KEY (workspace_id, run_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_leases_expiry
    ON omnix_agent_worker_leases (lease_expires_at, workspace_id, run_id);
