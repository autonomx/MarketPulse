-- omnix-migration: phase=expand transactional=true
-- TVP-9.1 (D-5): each US filer's trailing fundamentals for the screener, from SEC XBRL frames (one request per concept
-- and period covers every filer): revenue over the last twelve months and the twelve before, net income and diluted
-- EPS over the last twelve months, and the latest shareholders' equity. Shared market data (no workspace_id).

CREATE TABLE IF NOT EXISTS omnix_trading_fundamental_snapshots (
    cik TEXT PRIMARY KEY,
    revenue_ttm NUMERIC,
    revenue_prev_ttm NUMERIC,
    net_income_ttm NUMERIC,
    eps_ttm NUMERIC,
    equity NUMERIC,
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
