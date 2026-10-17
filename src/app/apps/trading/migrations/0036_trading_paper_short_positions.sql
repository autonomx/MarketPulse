-- Allow paper sell orders to reverse a long position into a short position.
-- A signed position quantity represents long (> 0) or short (< 0); the
-- average_cost remains the absolute entry price for either direction.

ALTER TABLE omnix_trading_paper_positions
    DROP CONSTRAINT IF EXISTS omnix_trading_paper_positions_quantity_check;
