ALTER TABLE omnix_trading_alerts
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_omnix_trading_alerts_expiration
    ON omnix_trading_alerts (
        workspace_id, enabled, expires_at, instrument_id, binding_id
    );
