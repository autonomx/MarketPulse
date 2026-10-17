-- Paper-order reservation hardening.
-- Keep free buying power and reserved buying power distinct, and prevent
-- multiple open sell orders from claiming the same position quantity.

ALTER TABLE omnix_trading_paper_positions
    ADD COLUMN IF NOT EXISTS reserved_quantity NUMERIC NOT NULL DEFAULT 0
    CHECK (reserved_quantity >= 0);

ALTER TABLE omnix_trading_paper_orders
    ADD COLUMN IF NOT EXISTS reserved_cash NUMERIC NOT NULL DEFAULT 0
    CHECK (reserved_cash >= 0);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_orders_reservations
    ON omnix_trading_paper_orders (
        workspace_id,
        account_id,
        status,
        side,
        instrument_id
    );
