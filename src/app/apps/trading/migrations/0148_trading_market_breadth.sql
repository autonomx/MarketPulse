-- omnix-migration: phase=expand transactional=true
-- TVP-6.6 (D-3): daily market breadth per exchange, computed in-house from Alpaca SIP daily bars over the active
-- listed universe: how many issues rose, fell or held, and the volume of the rising and falling ones. Market data,
-- the same for every workspace (no workspace_id, so no row-level policy), written by the breadth collector.

CREATE TABLE IF NOT EXISTS omnix_trading_market_breadth (
    exchange TEXT NOT NULL CHECK (exchange IN ('NYSE', 'NASDAQ')),
    session_date DATE NOT NULL,
    advances INTEGER NOT NULL CHECK (advances >= 0),
    declines INTEGER NOT NULL CHECK (declines >= 0),
    unchanged INTEGER NOT NULL CHECK (unchanged >= 0),
    advancing_volume NUMERIC NOT NULL CHECK (advancing_volume >= 0),
    declining_volume NUMERIC NOT NULL CHECK (declining_volume >= 0),
    source TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (exchange, session_date)
);
