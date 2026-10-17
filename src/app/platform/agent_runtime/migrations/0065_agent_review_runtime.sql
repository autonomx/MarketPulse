-- Durable independent-review execution attempts and hierarchical child resource grants.
-- ReviewAttempt is execution/protocol evidence only. A substantive ReviewResult is
-- persisted separately and only after a valid structured reviewer verdict exists.

CREATE TABLE IF NOT EXISTS omnix_agent_review_attempts (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    review_attempt_id TEXT NOT NULL,
    reviewer_run_id TEXT NOT NULL,
    review_snapshot_id TEXT NOT NULL,
    task_revision_id TEXT,
    workspace_state_id TEXT NOT NULL,
    reviewer_slot INTEGER NOT NULL CHECK (reviewer_slot >= 0),
    runtime_attempt INTEGER NOT NULL CHECK (runtime_attempt >= 1),
    protocol_version TEXT NOT NULL,
    model_provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    reasoning_effort TEXT,
    status TEXT NOT NULL,
    failure_class TEXT,
    failure_reason TEXT,
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, review_attempt_id),
    UNIQUE (workspace_id, reviewer_run_id),
    UNIQUE (workspace_id, run_id, review_snapshot_id, reviewer_slot, runtime_attempt),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, run_id, review_snapshot_id)
        REFERENCES omnix_agent_review_snapshots(workspace_id, run_id, snapshot_id) ON DELETE CASCADE,
    CHECK (status IN (
        'running','completed','runtime_failed','protocol_failed','cancelled'
    ))
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_review_attempts_snapshot
    ON omnix_agent_review_attempts (
        workspace_id, run_id, review_snapshot_id, reviewer_slot, runtime_attempt
    );

CREATE INDEX IF NOT EXISTS idx_omnix_agent_review_attempts_reviewer
    ON omnix_agent_review_attempts (workspace_id, reviewer_run_id);

CREATE TABLE IF NOT EXISTS omnix_agent_resource_grants (
    workspace_id TEXT NOT NULL,
    parent_run_id TEXT NOT NULL,
    child_run_id TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    max_steps INTEGER NOT NULL CHECK (max_steps >= 1),
    max_tool_calls INTEGER NOT NULL CHECK (max_tool_calls >= 1),
    max_tokens INTEGER,
    max_cost DOUBLE PRECISION,
    max_wall_time_seconds INTEGER NOT NULL CHECK (max_wall_time_seconds >= 1),
    state TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    released_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, parent_run_id, grant_id),
    UNIQUE (workspace_id, child_run_id),
    FOREIGN KEY (workspace_id, parent_run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, child_run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    CHECK (state IN ('active','released','exhausted')),
    CHECK (max_tokens IS NULL OR max_tokens >= 1),
    CHECK (max_cost IS NULL OR max_cost >= 0)
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_resource_grants_parent
    ON omnix_agent_resource_grants (workspace_id, parent_run_id, state, created_at);
