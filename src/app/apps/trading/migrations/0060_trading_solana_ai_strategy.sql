
CREATE TABLE IF NOT EXISTS omnix_trading_solana_ai_strategies (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    strategy_kind TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    display_name TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    chart_interval TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode = 'shadow'),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, strategy_id)
);

CREATE TABLE IF NOT EXISTS omnix_trading_solana_ai_decisions (
    workspace_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    state TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, strategy_id, event_id),
    UNIQUE (workspace_id, strategy_id, idempotency_key),
    FOREIGN KEY (workspace_id, strategy_id)
        REFERENCES omnix_trading_solana_ai_strategies(workspace_id, strategy_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_solana_ai_decisions_recent
    ON omnix_trading_solana_ai_decisions (workspace_id, strategy_id, observed_at DESC, created_at DESC);
