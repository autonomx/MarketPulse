CREATE TABLE IF NOT EXISTS omnix_trading_datasets (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    dataset_id TEXT NOT NULL,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    instrument_id TEXT NOT NULL,
    requested_binding_id TEXT,
    resolved_binding_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    interval TEXT NOT NULL,
    adjustment_mode TEXT NOT NULL,
    session_calendar TEXT NOT NULL,
    exchange_timezone TEXT NOT NULL,
    gap_policy TEXT NOT NULL CHECK (gap_policy IN ('fail', 'skip')),
    dataset_fingerprint TEXT NOT NULL,
    source_as_of TIMESTAMPTZ NOT NULL,
    bars JSONB NOT NULL,
    bar_count INTEGER NOT NULL CHECK (bar_count >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, dataset_id),
    UNIQUE (workspace_id, dataset_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_datasets_recent
    ON omnix_trading_datasets (workspace_id, created_at DESC, instrument_id);

CREATE TABLE IF NOT EXISTS omnix_trading_backtest_runs (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_parameters JSONB NOT NULL,
    execution_policy JSONB NOT NULL,
    formula_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    initial_cash NUMERIC NOT NULL,
    final_equity NUMERIC NOT NULL,
    total_return_percent NUMERIC NOT NULL,
    max_drawdown_percent NUMERIC NOT NULL,
    trade_count INTEGER NOT NULL,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id),
    FOREIGN KEY (workspace_id, dataset_id)
        REFERENCES omnix_trading_datasets(workspace_id, dataset_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_backtest_runs_recent
    ON omnix_trading_backtest_runs (workspace_id, dataset_id, created_at DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_backtest_trades (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    trade_index INTEGER NOT NULL CHECK (trade_index >= 0),
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    signal_time TIMESTAMPTZ NOT NULL,
    fill_time TIMESTAMPTZ NOT NULL,
    quantity NUMERIC NOT NULL,
    fill_price NUMERIC NOT NULL,
    commission NUMERIC NOT NULL,
    cash_after NUMERIC NOT NULL,
    position_after NUMERIC NOT NULL,
    PRIMARY KEY (workspace_id, run_id, trade_index),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_trading_backtest_runs(workspace_id, run_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_trading_backtest_equity (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    point_index INTEGER NOT NULL CHECK (point_index >= 0),
    bar_time TIMESTAMPTZ NOT NULL,
    cash NUMERIC NOT NULL,
    position NUMERIC NOT NULL,
    equity NUMERIC NOT NULL,
    drawdown_percent NUMERIC NOT NULL,
    PRIMARY KEY (workspace_id, run_id, point_index),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_trading_backtest_runs(workspace_id, run_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_trading_backtest_logs (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    log_index INTEGER NOT NULL CHECK (log_index >= 0),
    bar_time TIMESTAMPTZ,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (workspace_id, run_id, log_index),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_trading_backtest_runs(workspace_id, run_id)
        ON DELETE CASCADE
);
