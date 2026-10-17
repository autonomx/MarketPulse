CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_fills_observation_liquidity
    ON omnix_trading_paper_fills (
        workspace_id,
        account_id,
        instrument_id,
        source_time,
        side
    );
