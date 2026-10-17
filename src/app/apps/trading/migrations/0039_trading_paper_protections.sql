CREATE TABLE IF NOT EXISTS omnix_trading_paper_protections (
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    binding_id TEXT,
    entry_order_id TEXT,
    exit_order_id TEXT,
    take_profit NUMERIC CHECK (take_profit IS NULL OR take_profit > 0),
    stop_loss NUMERIC CHECK (stop_loss IS NULL OR stop_loss > 0),
    status TEXT NOT NULL CHECK (status IN ('pending_entry', 'active', 'exit_submitted', 'closed', 'cancelled')),
    trigger_reason TEXT,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, account_id, instrument_id),
    FOREIGN KEY (workspace_id, account_id)
        REFERENCES omnix_trading_paper_accounts(workspace_id, account_id)
        ON DELETE CASCADE,
    CHECK (take_profit IS NOT NULL OR stop_loss IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_protections_active
    ON omnix_trading_paper_protections (workspace_id, account_id, status, instrument_id);
