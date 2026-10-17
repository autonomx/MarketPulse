-- omnix-migration: phase=expand transactional=true
-- TVP-1.1 and TVP-1.2: alert frequencies, the condition model and channel settings.
--
-- * frequency: once, every_time, once_per_bar, once_per_bar_close or
--   once_per_minute, enforced by the server. Alerts that approximated
--   "once" and "once per bar" with a cooldown get the real frequency and
--   their approximating cooldown is cleared.
-- * omnix_trading_alert_conditions: one to five ordered conditions per
--   alert (source, operator, target, or amount and bars for the moving
--   operators). Existing alerts become one-condition rows with the meaning
--   they have always had; condition_type stays readable.
-- * condition_type accepts 'conditions' for alerts described only by their
--   conditions (threshold 0).
-- * notification_settings holds how an alert notifies (message, channels,
--   and webhook/email/sound settings), apart from condition_parameters, so
--   editing it keeps the alert's trigger history. Webhook secrets are kept
--   out of PostgreSQL.
-- * allow_partial_bars follows the frequency: only once_per_bar_close waits
--   for closed bars. Clients always sent false, which made every frequency
--   bar-close only.
-- * Legacy alerts that get their condition here count as edited now, so a
--   bar that closed before this migration cannot trigger them.
-- * definition_revision counts changes to what an alert watches; the
--   lifecycle trigger bumps it on a revisioned edit that changes the
--   definition (the application bumps it when the conditions change).
--   Per-bar trigger keys use it, so notification and lifecycle edits cannot
--   let the same bar trigger twice.
-- * The lifecycle-history trigger from 0027 now acts only on revisioned
--   edits. It also matched the evaluator's own state update, so the
--   observed value and the last trigger time were never kept.

ALTER TABLE omnix_trading_alerts
    ADD COLUMN IF NOT EXISTS definition_revision BIGINT NOT NULL DEFAULT 1
        CHECK (definition_revision >= 1);

ALTER TABLE omnix_trading_alerts
    ADD COLUMN IF NOT EXISTS frequency TEXT NOT NULL DEFAULT 'every_time';

CREATE OR REPLACE FUNCTION omnix_preserve_trading_alert_lifecycle_history()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.revision IS DISTINCT FROM OLD.revision
       AND NEW.instrument_id IS NOT DISTINCT FROM OLD.instrument_id
       AND NEW.binding_id IS NOT DISTINCT FROM OLD.binding_id
       AND NEW.condition_type IS NOT DISTINCT FROM OLD.condition_type
       AND NEW.threshold IS NOT DISTINCT FROM OLD.threshold
       AND NEW.condition_parameters IS NOT DISTINCT FROM OLD.condition_parameters
       AND NEW.evaluation_policy IS NOT DISTINCT FROM OLD.evaluation_policy
       AND NEW.frequency IS NOT DISTINCT FROM OLD.frequency
    THEN
        NEW.last_observed_price := OLD.last_observed_price;
        NEW.last_observed_value := OLD.last_observed_value;
        NEW.last_triggered_at := OLD.last_triggered_at;
    ELSIF NEW.revision IS DISTINCT FROM OLD.revision THEN
        NEW.definition_revision := OLD.definition_revision + 1;
    END IF;
    RETURN NEW;
END;
$$;


ALTER TABLE omnix_trading_alerts
    ADD COLUMN IF NOT EXISTS notification_settings JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE omnix_trading_alerts
   SET notification_settings = notification_settings || jsonb_strip_nulls(jsonb_build_object(
           'message', condition_parameters->'message',
           'notification_channels', condition_parameters->'notification_channels',
           'delivery', condition_parameters->'delivery'
       )),
       condition_parameters = condition_parameters - 'message' - 'notification_channels' - 'delivery'
 WHERE condition_parameters ?| ARRAY['message', 'notification_channels', 'delivery'];

ALTER TABLE omnix_trading_alerts
    DROP CONSTRAINT IF EXISTS omnix_trading_alerts_frequency_check;

ALTER TABLE omnix_trading_alerts
    ADD CONSTRAINT omnix_trading_alerts_frequency_check CHECK (
        frequency IN (
            'once', 'every_time', 'once_per_bar', 'once_per_bar_close', 'once_per_minute'
        )
    );

-- "Once" alerts that already fired stay stopped: they used to be held back by
-- a one-year cooldown, which is cleared here.
UPDATE omnix_trading_alerts
   SET frequency = 'once',
       cooldown_seconds = 0,
       enabled = CASE WHEN last_triggered_at IS NOT NULL THEN FALSE ELSE enabled END
 WHERE frequency = 'every_time'
   AND condition_parameters->>'trigger_policy' = 'once';

UPDATE omnix_trading_alerts
   SET frequency = 'once_per_bar',
       cooldown_seconds = 0
 WHERE frequency = 'every_time'
   AND condition_parameters->>'trigger_policy' = 'once_per_bar';

UPDATE omnix_trading_alerts
   SET evaluation_policy = jsonb_set(
           evaluation_policy, '{allow_partial_bars}', to_jsonb(frequency <> 'once_per_bar_close'), true
       )
 WHERE (evaluation_policy->'allow_partial_bars') IS DISTINCT FROM to_jsonb(frequency <> 'once_per_bar_close');

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
            'trendline_crossing_down', 'trendline_above', 'trendline_below',
            'conditions'
        )
    );

CREATE TABLE IF NOT EXISTS omnix_trading_alert_conditions (
    workspace_id TEXT NOT NULL,
    alert_id TEXT NOT NULL,
    position SMALLINT NOT NULL CHECK (position BETWEEN 0 AND 4),
    source JSONB NOT NULL,
    operator TEXT NOT NULL CHECK (
        operator IN (
            'crossing', 'crossing_up', 'crossing_down',
            'greater_than', 'less_than',
            'entering_channel', 'exiting_channel', 'inside_channel', 'outside_channel',
            'moving_up', 'moving_down', 'moving_up_percent', 'moving_down_percent'
        )
    ),
    target JSONB,
    amount NUMERIC,
    bars INTEGER CHECK (bars IS NULL OR bars >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, alert_id, position),
    FOREIGN KEY (workspace_id, alert_id)
        REFERENCES omnix_trading_alerts(workspace_id, alert_id)
        ON DELETE CASCADE,
    CHECK (
        (operator LIKE 'moving\_%' AND target IS NULL AND amount > 0 AND bars IS NOT NULL)
        OR (operator NOT LIKE 'moving\_%' AND target IS NOT NULL AND amount IS NULL AND bars IS NULL)
    )
);

ALTER TABLE omnix_trading_alert_conditions ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_trading_alert_conditions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_trading_alert_conditions;
CREATE POLICY tenant_isolation ON omnix_trading_alert_conditions
    USING (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on')
    WITH CHECK (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on');

UPDATE omnix_trading_alerts AS alert
   SET updated_at = CURRENT_TIMESTAMP
 WHERE alert.condition_type <> 'conditions'
   AND NOT EXISTS (
       SELECT 1
         FROM omnix_trading_alert_conditions AS existing
        WHERE existing.workspace_id = alert.workspace_id
          AND existing.alert_id = alert.alert_id
   );

-- Existing alerts become one condition each. This mirrors legacy_conditions()
-- in app/apps/trading/alert_conditions.py; every legacy family fires on a
-- crossing, "_above" upwards and "_below" downwards.
INSERT INTO omnix_trading_alert_conditions (workspace_id, alert_id, position, source, operator, target)
SELECT alert.workspace_id, alert.alert_id, 0, mapped.source, mapped.operator, mapped.target
  FROM omnix_trading_alerts AS alert
 CROSS JOIN LATERAL (
    SELECT
        COALESCE((alert.condition_parameters->>'period')::INTEGER, 14) AS period,
        COALESCE((alert.condition_parameters->>'fast_period')::INTEGER, 12) AS fast_period,
        COALESCE((alert.condition_parameters->>'slow_period')::INTEGER, 26) AS slow_period,
        COALESCE((alert.condition_parameters->>'signal_period')::INTEGER, 9) AS signal_period,
        COALESCE((alert.condition_parameters->>'lookback_bars')::INTEGER, 1) AS lookback_bars,
        COALESCE((alert.condition_parameters->>'anchor_bars_ago')::INTEGER, 0) AS anchor_bars_ago,
        COALESCE(alert.condition_parameters->>'component', 'value') AS component,
        alert.condition_parameters->>'indicator_id' AS indicator_id
 ) AS legacy
 CROSS JOIN LATERAL (
    SELECT
        CASE
            WHEN alert.condition_type LIKE 'price\_%' OR alert.condition_type LIKE 'trendline\_%'
                THEN jsonb_build_object('kind', 'price', 'field', 'close')
            WHEN alert.condition_type LIKE 'volume\_%'
                THEN jsonb_build_object('kind', 'price', 'field', 'volume')
            WHEN alert.condition_type LIKE 'percent\_change\_%'
                THEN jsonb_build_object('kind', 'change_percent', 'lookback_bars', legacy.lookback_bars)
            WHEN legacy.indicator_id IN ('sma', 'ema', 'rsi', 'atr')
                THEN jsonb_build_object(
                    'kind', 'indicator', 'indicator_id', legacy.indicator_id,
                    'inputs', jsonb_build_object('period', legacy.period),
                    'output', legacy.indicator_id || ':' || legacy.period
                )
            WHEN legacy.indicator_id = 'bollinger'
                THEN jsonb_build_object(
                    'kind', 'indicator', 'indicator_id', 'bollinger',
                    'inputs', jsonb_build_object('period', legacy.period, 'standard_deviations', 2.0),
                    'output', 'bollinger:' || legacy.period || ':'
                        || CASE WHEN legacy.component IN ('upper', 'middle', 'lower') THEN legacy.component ELSE 'middle' END
                )
            WHEN legacy.indicator_id = 'macd'
                THEN jsonb_build_object(
                    'kind', 'indicator', 'indicator_id', 'macd',
                    'inputs', jsonb_build_object(
                        'period', legacy.period, 'fast_period', legacy.fast_period,
                        'slow_period', legacy.slow_period, 'signal_period', legacy.signal_period
                    ),
                    'output', 'macd:' || legacy.fast_period || ':' || legacy.slow_period || ':'
                        || CASE WHEN legacy.component IN ('line', 'signal', 'histogram') THEN legacy.component ELSE 'line' END
                )
            WHEN legacy.indicator_id = 'stochastic-rsi'
                THEN jsonb_build_object(
                    'kind', 'indicator', 'indicator_id', 'stochastic-rsi',
                    'inputs', jsonb_build_object(
                        'period', legacy.period, 'fast_period', legacy.fast_period,
                        'signal_period', legacy.signal_period
                    ),
                    'output', 'stochastic-rsi:k'
                )
            WHEN legacy.indicator_id = 'vwap'
                THEN jsonb_build_object(
                    'kind', 'indicator', 'indicator_id', 'vwap',
                    'inputs', jsonb_build_object('period', 1, 'anchor_bars_ago', legacy.anchor_bars_ago),
                    'output', 'vwap:dataset'
                )
        END AS source,
        CASE alert.condition_type
            WHEN 'trendline_crossing' THEN 'crossing'
            WHEN 'trendline_crossing_up' THEN 'crossing_up'
            WHEN 'trendline_crossing_down' THEN 'crossing_down'
            WHEN 'trendline_above' THEN 'crossing_up'
            WHEN 'trendline_below' THEN 'crossing_down'
            ELSE CASE WHEN alert.condition_type LIKE '%\_above' THEN 'crossing_up' ELSE 'crossing_down' END
        END AS operator,
        CASE
            WHEN alert.condition_type LIKE 'trendline\_%'
                THEN jsonb_build_object(
                    'kind', 'source',
                    'source', jsonb_build_object(
                        'kind', 'trendline',
                        'points', alert.condition_parameters->'trendline_points'
                    )
                )
            ELSE jsonb_build_object('kind', 'value', 'value', alert.threshold::TEXT)
        END AS target
 ) AS mapped
 WHERE alert.condition_type <> 'conditions'
   AND mapped.source IS NOT NULL
   AND (
       alert.condition_type NOT LIKE 'trendline\_%'
       OR jsonb_typeof(alert.condition_parameters->'trendline_points') = 'array'
   )
   AND NOT EXISTS (
       SELECT 1
         FROM omnix_trading_alert_conditions AS existing
        WHERE existing.workspace_id = alert.workspace_id
          AND existing.alert_id = alert.alert_id
   )
ON CONFLICT DO NOTHING;
