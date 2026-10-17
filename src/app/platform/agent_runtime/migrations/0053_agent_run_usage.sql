-- Durable generalized-agent resource accounting.
-- Counters are PostgreSQL-authoritative so worker restart cannot reset budgets.

CREATE TABLE IF NOT EXISTS omnix_agent_run_usage (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    steps BIGINT NOT NULL DEFAULT 0 CHECK (steps >= 0),
    tool_calls BIGINT NOT NULL DEFAULT 0 CHECK (tool_calls >= 0),
    model_calls BIGINT NOT NULL DEFAULT 0 CHECK (model_calls >= 0),
    output_tokens BIGINT NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cost NUMERIC(20, 6) NOT NULL DEFAULT 0 CHECK (cost >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE
);
