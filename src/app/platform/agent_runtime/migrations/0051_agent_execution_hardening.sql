-- Agent runtime execution hardening.
-- PostgreSQL owns broker execution idempotency/outcome state for AgentRuntime runs.

CREATE TABLE IF NOT EXISTS omnix_agent_capability_executions (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    execution_key TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'created',
    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    state_changed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, execution_key),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    CHECK (state IN ('created','waiting_for_approval','running','completed','failed'))
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_capability_executions_state
    ON omnix_agent_capability_executions (workspace_id, run_id, state, updated_at);
