CREATE TABLE IF NOT EXISTS omnix_trading_paper_accounts (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    account_id TEXT NOT NULL,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    commission_bps NUMERIC NOT NULL DEFAULT 0 CHECK (commission_bps BETWEEN 0 AND 1000),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, account_id)
);

CREATE TABLE IF NOT EXISTS omnix_trading_paper_balances (
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    available NUMERIC NOT NULL DEFAULT 0,
    reserved NUMERIC NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, account_id, currency),
    FOREIGN KEY (workspace_id, account_id)
        REFERENCES omnix_trading_paper_accounts(workspace_id, account_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_trading_paper_positions (
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    quantity NUMERIC NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    average_cost NUMERIC NOT NULL DEFAULT 0 CHECK (average_cost >= 0),
    realized_pnl NUMERIC NOT NULL DEFAULT 0,
    last_price NUMERIC,
    unrealized_pnl NUMERIC NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, account_id, instrument_id),
    FOREIGN KEY (workspace_id, account_id)
        REFERENCES omnix_trading_paper_accounts(workspace_id, account_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_trading_paper_orders (
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    binding_id TEXT,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type TEXT NOT NULL CHECK (order_type IN ('market', 'limit', 'stop')),
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    limit_price NUMERIC,
    stop_price NUMERIC,
    status TEXT NOT NULL CHECK (status IN ('open', 'filled', 'cancelled', 'rejected')),
    filled_quantity NUMERIC NOT NULL DEFAULT 0 CHECK (filled_quantity >= 0),
    average_fill_price NUMERIC,
    idempotency_key TEXT NOT NULL,
    rejection_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, account_id, order_id),
    UNIQUE (workspace_id, account_id, idempotency_key),
    FOREIGN KEY (workspace_id, account_id)
        REFERENCES omnix_trading_paper_accounts(workspace_id, account_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_orders_open
    ON omnix_trading_paper_orders (workspace_id, account_id, status, instrument_id, created_at);

CREATE TABLE IF NOT EXISTS omnix_trading_paper_fills (
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    fill_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity NUMERIC NOT NULL,
    price NUMERIC NOT NULL,
    commission NUMERIC NOT NULL,
    source_time TIMESTAMPTZ NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, account_id, fill_id),
    UNIQUE (workspace_id, account_id, idempotency_key),
    FOREIGN KEY (workspace_id, account_id, order_id)
        REFERENCES omnix_trading_paper_orders(workspace_id, account_id, order_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_trading_paper_ledger (
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    ledger_id TEXT NOT NULL,
    entry_type TEXT NOT NULL CHECK (entry_type IN ('deposit', 'withdrawal', 'trade_cash', 'commission', 'realized_pnl')),
    currency TEXT NOT NULL,
    amount NUMERIC NOT NULL,
    order_id TEXT,
    fill_id TEXT,
    idempotency_key TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, account_id, ledger_id),
    UNIQUE (workspace_id, account_id, idempotency_key),
    FOREIGN KEY (workspace_id, account_id)
        REFERENCES omnix_trading_paper_accounts(workspace_id, account_id)
        ON DELETE CASCADE
);
