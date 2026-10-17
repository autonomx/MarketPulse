ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS win_rate_percent NUMERIC NOT NULL DEFAULT 0;

ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS exposure_percent NUMERIC NOT NULL DEFAULT 0;

ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS ending_cash NUMERIC;

ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS ending_position NUMERIC;

ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS ending_mark_price NUMERIC;

ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS realized_pnl NUMERIC;

ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS unrealized_pnl NUMERIC;

ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS mark_to_market_policy TEXT;

ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS economic_result_fingerprint TEXT;

ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS artifact_storage_provider TEXT;

ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS artifact_storage_key TEXT;

ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS artifact_checksum_sha256 TEXT;

ALTER TABLE omnix_trading_backtest_runs
    ADD COLUMN IF NOT EXISTS artifact_byte_size BIGINT;

ALTER TABLE omnix_trading_backtest_runs
    DROP CONSTRAINT IF EXISTS omnix_trading_backtest_runs_metric_bounds;

ALTER TABLE omnix_trading_backtest_runs
    ADD CONSTRAINT omnix_trading_backtest_runs_metric_bounds
    CHECK (
        win_rate_percent BETWEEN 0 AND 100
        AND exposure_percent BETWEEN 0 AND 100
    );

ALTER TABLE omnix_trading_backtest_runs
    DROP CONSTRAINT IF EXISTS omnix_trading_backtest_runs_mark_policy;

ALTER TABLE omnix_trading_backtest_runs
    ADD CONSTRAINT omnix_trading_backtest_runs_mark_policy
    CHECK (
        mark_to_market_policy IS NULL
        OR mark_to_market_policy = 'final_finalized_bar_close'
    );

ALTER TABLE omnix_trading_backtest_runs
    DROP CONSTRAINT IF EXISTS omnix_trading_backtest_runs_economic_fingerprint;

ALTER TABLE omnix_trading_backtest_runs
    ADD CONSTRAINT omnix_trading_backtest_runs_economic_fingerprint
    CHECK (
        economic_result_fingerprint IS NULL
        OR length(economic_result_fingerprint) = 64
    );

ALTER TABLE omnix_trading_backtest_runs
    DROP CONSTRAINT IF EXISTS omnix_trading_backtest_runs_artifact_complete;

ALTER TABLE omnix_trading_backtest_runs
    ADD CONSTRAINT omnix_trading_backtest_runs_artifact_complete
    CHECK (
        (
            artifact_storage_provider IS NULL
            AND artifact_storage_key IS NULL
            AND artifact_checksum_sha256 IS NULL
            AND artifact_byte_size IS NULL
        )
        OR (
            artifact_storage_provider IS NOT NULL
            AND artifact_storage_key IS NOT NULL
            AND length(artifact_checksum_sha256) = 64
            AND artifact_byte_size > 0
        )
    );

CREATE INDEX IF NOT EXISTS idx_omnix_trading_backtest_economic_fingerprint
    ON omnix_trading_backtest_runs (workspace_id, economic_result_fingerprint)
    WHERE economic_result_fingerprint IS NOT NULL;
