CREATE TABLE IF NOT EXISTS omnix_trading_scanners (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    scanner_id TEXT NOT NULL,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    instrument_ids JSONB NOT NULL,
    binding_ids JSONB NOT NULL DEFAULT '{}'::jsonb,
    interval TEXT NOT NULL,
    history_limit INTEGER NOT NULL CHECK (history_limit BETWEEN 2 AND 500),
    rules JSONB NOT NULL,
    max_concurrency INTEGER NOT NULL CHECK (max_concurrency BETWEEN 1 AND 8),
    request_timeout_seconds NUMERIC NOT NULL CHECK (request_timeout_seconds BETWEEN 1 AND 30),
    run_timeout_seconds NUMERIC NOT NULL CHECK (run_timeout_seconds BETWEEN 1 AND 300),
    formula_version TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, scanner_id)
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_scanners_recent
    ON omnix_trading_scanners (workspace_id, enabled, updated_at DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_scanner_runs (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    scanner_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'timed_out')),
    cancellation_requested BOOLEAN NOT NULL DEFAULT FALSE,
    universe_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    matched_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_message TEXT,
    definition_snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id),
    FOREIGN KEY (workspace_id, scanner_id)
        REFERENCES omnix_trading_scanners(workspace_id, scanner_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_scanner_runs_recent
    ON omnix_trading_scanner_runs (workspace_id, scanner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_scanner_results (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    requested_binding_id TEXT,
    resolved_binding_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    dataset_fingerprint TEXT NOT NULL,
    source_as_of TIMESTAMPTZ NOT NULL,
    formula_version TEXT NOT NULL,
    metrics JSONB NOT NULL,
    matched_rules JSONB NOT NULL,
    rank INTEGER NOT NULL CHECK (rank >= 1),
    score NUMERIC NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, instrument_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_trading_scanner_runs(workspace_id, run_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_scanner_results_rank
    ON omnix_trading_scanner_results (workspace_id, run_id, rank, instrument_id);
