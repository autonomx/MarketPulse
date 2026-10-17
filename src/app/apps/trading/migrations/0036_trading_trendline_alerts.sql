ALTER TABLE omnix_trading_alerts
    DROP CONSTRAINT IF EXISTS omnix_trading_alerts_condition_type_check;

ALTER TABLE omnix_trading_alerts
    ADD CONSTRAINT omnix_trading_alerts_condition_type_check CHECK (
        condition_type IN (
            'price_above', 'price_below',
            'percent_change_above', 'percent_change_below',
            'indicator_above', 'indicator_below',
            'indicator_cross_above', 'indicator_cross_below',
            'volume_above', 'volume_below',
            'trendline_crossing', 'trendline_crossing_up',
            'trendline_crossing_down', 'trendline_above', 'trendline_below'
        )
    );
