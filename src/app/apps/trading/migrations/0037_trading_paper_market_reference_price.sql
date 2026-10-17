-- Allow market orders to reserve their quoted notional instead of all free cash.

ALTER TABLE omnix_trading_paper_orders
    ADD COLUMN IF NOT EXISTS reference_price NUMERIC
    CHECK (reference_price IS NULL OR reference_price > 0);
