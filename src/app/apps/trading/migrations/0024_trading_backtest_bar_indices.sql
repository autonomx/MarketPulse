ALTER TABLE omnix_trading_backtest_trades
    ADD COLUMN IF NOT EXISTS signal_bar_index INTEGER;

ALTER TABLE omnix_trading_backtest_trades
    ADD COLUMN IF NOT EXISTS fill_bar_index INTEGER;

ALTER TABLE omnix_trading_backtest_trades
    DROP CONSTRAINT IF EXISTS omnix_trading_backtest_trades_next_bar_check;

ALTER TABLE omnix_trading_backtest_trades
    ADD CONSTRAINT omnix_trading_backtest_trades_next_bar_check
    CHECK (
        (signal_bar_index IS NULL AND fill_bar_index IS NULL)
        OR (
            signal_bar_index >= 0
            AND fill_bar_index = signal_bar_index + 1
        )
    );

COMMENT ON COLUMN omnix_trading_backtest_trades.signal_bar_index IS
    'Exact frozen-dataset bar index that produced the signal. NULL only for legacy rows created before OTT-12 sequencing evidence.';

COMMENT ON COLUMN omnix_trading_backtest_trades.fill_bar_index IS
    'Exact frozen-dataset bar index used for the fill. New rows require next-bar sequencing in the application contract; NULL is retained for legacy rows rather than fabricated.';
