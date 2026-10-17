-- omnix-migration: phase=expand transactional=true
-- TVP-7.1: stop-limit and trailing stop paper orders, time in force (DAY, GTC,
-- GTD), and trailing stop-loss bracket legs. Every new column is nullable or
-- defaults to the behaviour before this migration (GTC, no trailing), so
-- existing orders, protections and strategy-owned accounts are unchanged.

ALTER TABLE omnix_trading_paper_orders
    DROP CONSTRAINT IF EXISTS omnix_trading_paper_orders_order_type_check;
ALTER TABLE omnix_trading_paper_orders
    ADD CONSTRAINT omnix_trading_paper_orders_order_type_check CHECK (
        order_type IN ('market', 'limit', 'stop', 'stop_limit', 'trailing_stop')
    );

ALTER TABLE omnix_trading_paper_orders
    DROP CONSTRAINT IF EXISTS omnix_trading_paper_orders_status_check;
ALTER TABLE omnix_trading_paper_orders
    ADD CONSTRAINT omnix_trading_paper_orders_status_check CHECK (
        status IN ('open', 'filled', 'cancelled', 'rejected', 'expired')
    );

ALTER TABLE omnix_trading_paper_orders
    ADD COLUMN IF NOT EXISTS time_in_force TEXT NOT NULL DEFAULT 'gtc';
-- DAY and GTD orders carry the moment they expire; DAY's is set at placement.
ALTER TABLE omnix_trading_paper_orders
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE omnix_trading_paper_orders
    ADD COLUMN IF NOT EXISTS trail_amount NUMERIC;
ALTER TABLE omnix_trading_paper_orders
    ADD COLUMN IF NOT EXISTS trail_percent NUMERIC;
-- Server-tracked trailing state: the best price since the order was armed.
-- stop_price holds the stop it implies.
ALTER TABLE omnix_trading_paper_orders
    ADD COLUMN IF NOT EXISTS trail_water_mark NUMERIC;
-- When a stop-limit order's stop was reached; it is a limit order from then on.
ALTER TABLE omnix_trading_paper_orders
    ADD COLUMN IF NOT EXISTS stop_triggered_at TIMESTAMPTZ;
-- When a trailing stop last moved: a bar that started earlier cannot trigger
-- it by its range, since its low may predate the high that moved the stop.
ALTER TABLE omnix_trading_paper_orders
    ADD COLUMN IF NOT EXISTS trail_moved_at TIMESTAMPTZ;

ALTER TABLE omnix_trading_paper_orders
    DROP CONSTRAINT IF EXISTS omnix_trading_paper_orders_time_in_force_check;
ALTER TABLE omnix_trading_paper_orders
    ADD CONSTRAINT omnix_trading_paper_orders_time_in_force_check CHECK (
        time_in_force IN ('gtc', 'day', 'gtd')
        AND (time_in_force = 'gtc') = (expires_at IS NULL)
    );

ALTER TABLE omnix_trading_paper_orders
    DROP CONSTRAINT IF EXISTS omnix_trading_paper_orders_trailing_check;
ALTER TABLE omnix_trading_paper_orders
    ADD CONSTRAINT omnix_trading_paper_orders_trailing_check CHECK (
        (order_type = 'trailing_stop') = (num_nonnulls(trail_amount, trail_percent) = 1)
        AND (trail_amount IS NULL OR trail_amount > 0)
        AND (trail_percent IS NULL OR (trail_percent > 0 AND trail_percent < 100))
        AND (trail_water_mark IS NULL OR order_type = 'trailing_stop')
        AND (trail_moved_at IS NULL OR order_type = 'trailing_stop')
    );

ALTER TABLE omnix_trading_paper_orders
    DROP CONSTRAINT IF EXISTS omnix_trading_paper_orders_stop_limit_check;
ALTER TABLE omnix_trading_paper_orders
    ADD CONSTRAINT omnix_trading_paper_orders_stop_limit_check CHECK (
        order_type <> 'stop_limit' OR (stop_price IS NOT NULL AND limit_price IS NOT NULL)
    );

CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_orders_expiry
    ON omnix_trading_paper_orders (workspace_id, account_id, expires_at)
    WHERE status = 'open' AND expires_at IS NOT NULL;

-- A stop-loss bracket leg may trail: the monitor raises (for a short, lowers)
-- stop_loss behind trail_water_mark, never past the stop it already has.
ALTER TABLE omnix_trading_paper_protections
    ADD COLUMN IF NOT EXISTS trail_amount NUMERIC;
ALTER TABLE omnix_trading_paper_protections
    ADD COLUMN IF NOT EXISTS trail_percent NUMERIC;
ALTER TABLE omnix_trading_paper_protections
    ADD COLUMN IF NOT EXISTS trail_water_mark NUMERIC;
ALTER TABLE omnix_trading_paper_protections
    ADD COLUMN IF NOT EXISTS trail_moved_at TIMESTAMPTZ;

ALTER TABLE omnix_trading_paper_protections
    DROP CONSTRAINT IF EXISTS omnix_trading_paper_protections_trailing_check;
ALTER TABLE omnix_trading_paper_protections
    ADD CONSTRAINT omnix_trading_paper_protections_trailing_check CHECK (
        num_nonnulls(trail_amount, trail_percent) <= 1
        AND (trail_amount IS NULL OR trail_amount > 0)
        AND (trail_percent IS NULL OR (trail_percent > 0 AND trail_percent < 100))
        AND (num_nonnulls(trail_amount, trail_percent) = 0 OR stop_loss IS NOT NULL)
    );
