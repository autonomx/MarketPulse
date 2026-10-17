-- omnix-migration: phase=expand transactional=true
-- WP-8.3: market evidence that trading decisions read moves from local JSON
-- files into PostgreSQL. Market evidence is the same for every workspace, so
-- these tables are not per workspace.

-- Yahoo 1-minute bars as revisions identified by content (every field but
-- received_at); received_at is the earliest time that content was seen.
CREATE TABLE IF NOT EXISTS omnix_trading_yahoo_bar_revisions (
    symbol TEXT NOT NULL,
    session_date DATE NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    content_sha256 TEXT NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume NUMERIC NOT NULL,
    session TEXT NOT NULL,
    provider_event_id TEXT,
    received_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (symbol, start_time, content_sha256)
);
CREATE INDEX IF NOT EXISTS idx_omnix_trading_yahoo_bar_revisions_session
    ON omnix_trading_yahoo_bar_revisions (symbol, session_date);

-- Monotonic evidence counters per provider, incremented atomically.
CREATE TABLE IF NOT EXISTS omnix_trading_evidence_counters (
    provider TEXT NOT NULL,
    name TEXT NOT NULL,
    value BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, name)
);

CREATE TABLE IF NOT EXISTS omnix_trading_evidence_session_counters (
    provider TEXT NOT NULL,
    session_date DATE NOT NULL,
    name TEXT NOT NULL,
    value BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider, session_date, name)
);

-- IBKR promotion evidence: one session's diagnostics (counts, sums, maxima),
-- read and rewritten whole under the row lock.
CREATE TABLE IF NOT EXISTS omnix_trading_ibkr_session_evidence (
    session_date DATE PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Files already imported by the one-off evidence import, so it can be re-run.
CREATE TABLE IF NOT EXISTS omnix_trading_evidence_imports (
    source TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
