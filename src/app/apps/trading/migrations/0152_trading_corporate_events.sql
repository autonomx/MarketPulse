-- omnix-migration: phase=expand transactional=true
-- TVP-10.1 (D-5): a US stock's earnings (SEC 8-K item 2.02 filings), dividends and splits (Alpaca corporate actions),
-- cached per ticker for a day. Shared market data (no workspace_id).

CREATE TABLE IF NOT EXISTS omnix_trading_corporate_events (
    ticker TEXT PRIMARY KEY,
    events JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
