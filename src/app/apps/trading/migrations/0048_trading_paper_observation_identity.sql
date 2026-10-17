ALTER TABLE omnix_trading_paper_fills
    ADD COLUMN IF NOT EXISTS observation_key TEXT;

DROP INDEX IF EXISTS idx_omnix_trading_paper_fills_observation_liquidity;

CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_fills_observation_liquidity_v2
    ON omnix_trading_paper_fills (
        workspace_id,
        account_id,
        instrument_id,
        observation_key,
        side
    )
    WHERE observation_key IS NOT NULL;
