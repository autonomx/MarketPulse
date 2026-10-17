-- Causal trade-attempt correlation for repeat-symbol-safe AUTO PAPER evidence.
--
-- 0045 introduced deterministic session/setup/intent correlation, but its setup
-- and intent keys were intentionally coarse (strategy + session + instrument).
-- That becomes ambiguous if a strategy ever permits more than one distinct
-- signal for the same symbol on the same trading day. New AUTO PAPER events now
-- persist a causal trade_attempt_id derived from the finalized signal time.
-- This migration upgrades those events/trades to trade-lifecycle-v2 while
-- leaving legacy rows safely queryable under their original v1 identities.

ALTER TABLE omnix_trading_strategy_events
    ADD COLUMN IF NOT EXISTS trade_attempt_id TEXT;
ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS trade_attempt_id TEXT;

CREATE INDEX IF NOT EXISTS idx_omnix_trading_strategy_events_trade_attempt
    ON omnix_trading_strategy_events (workspace_id, strategy_id, trade_attempt_id, observed_at)
    WHERE trade_attempt_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_trade_records_trade_attempt
    ON omnix_trading_paper_trade_records (workspace_id, account_id, trade_attempt_id, entry_time)
    WHERE trade_attempt_id IS NOT NULL;

CREATE OR REPLACE FUNCTION omnix_trading_stamp_strategy_event_correlation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    session_date_value DATE;
    current_revision BIGINT;
    risk_payload JSONB;
    payload_attempt TEXT;
BEGIN
    session_date_value := (NEW.observed_at AT TIME ZONE 'America/New_York')::DATE;

    IF NEW.strategy_revision IS NULL THEN
        SELECT revision
          INTO current_revision
          FROM omnix_trading_strategy_configs
         WHERE workspace_id = NEW.workspace_id
           AND strategy_id = NEW.strategy_id;
        NEW.strategy_revision := current_revision;
    END IF;

    payload_attempt := NULLIF(NEW.payload ->> 'trade_attempt_id', '');
    NEW.trade_attempt_id := COALESCE(NEW.trade_attempt_id, payload_attempt);
    NEW.correlation_version := CASE
        WHEN NEW.trade_attempt_id IS NOT NULL THEN 'trade-lifecycle-v2'
        ELSE COALESCE(NEW.correlation_version, 'trade-lifecycle-v1')
    END;
    NEW.session_id := COALESCE(
        NEW.session_id,
        'session-' || SUBSTRING(
            MD5(NEW.workspace_id || '|' || NEW.strategy_id || '|' || session_date_value::TEXT),
            1,
            24
        )
    );

    IF NEW.trade_attempt_id IS NOT NULL THEN
        NEW.setup_id := 'setup-' || SUBSTRING(
            MD5(
                NEW.workspace_id || '|' || NEW.strategy_id || '|'
                || NEW.trade_attempt_id || '|setup'
            ),
            1,
            24
        );
        NEW.trade_intent_id := 'intent-' || SUBSTRING(
            MD5(
                NEW.workspace_id || '|' || NEW.strategy_id || '|'
                || NEW.trade_attempt_id || '|long-entry'
            ),
            1,
            24
        );
    ELSE
        NEW.setup_id := COALESCE(
            NEW.setup_id,
            'setup-' || SUBSTRING(
                MD5(
                    NEW.workspace_id || '|' || NEW.strategy_id || '|' || session_date_value::TEXT
                    || '|' || NEW.instrument_id
                ),
                1,
                24
            )
        );
        NEW.trade_intent_id := COALESCE(
            NEW.trade_intent_id,
            'intent-' || SUBSTRING(
                MD5(
                    NEW.workspace_id || '|' || NEW.strategy_id || '|' || session_date_value::TEXT
                    || '|' || NEW.instrument_id || '|long-entry'
                ),
                1,
                24
            )
        );
    END IF;

    risk_payload := NEW.payload -> 'risk_decision';
    IF risk_payload IS NOT NULL AND risk_payload <> 'null'::JSONB THEN
        NEW.risk_decision_id := 'risk-' || SUBSTRING(
            MD5(NEW.trade_intent_id || '|' || risk_payload::TEXT),
            1,
            24
        );
    END IF;

    NEW.payload := COALESCE(NEW.payload, '{}'::JSONB)
        || JSONB_STRIP_NULLS(
            JSONB_BUILD_OBJECT(
                'correlation_version', NEW.correlation_version,
                'strategy_revision', NEW.strategy_revision,
                'session_id', NEW.session_id,
                'setup_id', NEW.setup_id,
                'trade_attempt_id', NEW.trade_attempt_id,
                'trade_intent_id', NEW.trade_intent_id,
                'risk_decision_id', NEW.risk_decision_id
            )
        );
    RETURN NEW;
END;
$$;

-- Historical entry submissions can safely recover an attempt boundary from the
-- exact durable order identity. Other historical state/research events cannot
-- be assigned to a particular attempt without inventing evidence, so they stay
-- on the v1 candidate/session correlation.
UPDATE omnix_trading_strategy_events
   SET trade_attempt_id = COALESCE(
           NULLIF(payload ->> 'trade_attempt_id', ''),
           CASE
               WHEN event_type = 'entry_order_submitted'
                    AND NULLIF(payload ->> 'order_id', '') IS NOT NULL
               THEN 'attempt-' || SUBSTRING(
                   MD5(
                       workspace_id || '|' || strategy_id || '|'
                       || (payload ->> 'order_id') || '|historical-entry'
                   ),
                   1,
                   24
               )
               ELSE NULL
           END
       )
 WHERE trade_attempt_id IS NULL;

UPDATE omnix_trading_strategy_events
   SET correlation_version = 'trade-lifecycle-v2',
       setup_id = 'setup-' || SUBSTRING(
           MD5(workspace_id || '|' || strategy_id || '|' || trade_attempt_id || '|setup'),
           1,
           24
       ),
       trade_intent_id = 'intent-' || SUBSTRING(
           MD5(workspace_id || '|' || strategy_id || '|' || trade_attempt_id || '|long-entry'),
           1,
           24
       )
 WHERE trade_attempt_id IS NOT NULL;

UPDATE omnix_trading_strategy_events
   SET risk_decision_id = 'risk-' || SUBSTRING(
           MD5(trade_intent_id || '|' || (payload -> 'risk_decision')::TEXT),
           1,
           24
       )
 WHERE trade_attempt_id IS NOT NULL
   AND payload ? 'risk_decision'
   AND payload -> 'risk_decision' <> 'null'::JSONB;

UPDATE omnix_trading_strategy_events
   SET payload = COALESCE(payload, '{}'::JSONB)
       || JSONB_STRIP_NULLS(
           JSONB_BUILD_OBJECT(
               'correlation_version', correlation_version,
               'strategy_revision', strategy_revision,
               'session_id', session_id,
               'setup_id', setup_id,
               'trade_attempt_id', trade_attempt_id,
               'trade_intent_id', trade_intent_id,
               'risk_decision_id', risk_decision_id
           )
       )
 WHERE trade_attempt_id IS NOT NULL;

CREATE OR REPLACE FUNCTION omnix_trading_correlate_paper_trade_record()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    event_row RECORD;
BEGIN
    IF NEW.entry_signal_event_id IS NOT NULL THEN
        SELECT run_id, strategy_revision, correlation_version, session_id,
               setup_id, trade_attempt_id, trade_intent_id, risk_decision_id
          INTO event_row
          FROM omnix_trading_strategy_events
         WHERE workspace_id = NEW.workspace_id
           AND strategy_id = NEW.strategy_id
           AND event_id = NEW.entry_signal_event_id
         LIMIT 1;

        IF FOUND THEN
            NEW.strategy_run_id := COALESCE(NEW.strategy_run_id, event_row.run_id);
            NEW.strategy_revision := COALESCE(NEW.strategy_revision, event_row.strategy_revision);
            NEW.session_id := COALESCE(NEW.session_id, event_row.session_id);

            -- A v2 attempt is one atomic correlation identity. Never pair a v2
            -- correlation_version/trade_attempt_id with legacy v1 setup/intent
            -- values that may already exist on an older trade row.
            IF event_row.trade_attempt_id IS NOT NULL THEN
                NEW.correlation_version := 'trade-lifecycle-v2';
                NEW.setup_id := event_row.setup_id;
                NEW.trade_attempt_id := event_row.trade_attempt_id;
                NEW.trade_intent_id := event_row.trade_intent_id;
                NEW.risk_decision_id := event_row.risk_decision_id;
            ELSE
                NEW.correlation_version := COALESCE(
                    NEW.correlation_version,
                    event_row.correlation_version,
                    'trade-lifecycle-v1'
                );
                NEW.setup_id := COALESCE(NEW.setup_id, event_row.setup_id);
                NEW.trade_attempt_id := COALESCE(NEW.trade_attempt_id, event_row.trade_attempt_id);
                NEW.trade_intent_id := COALESCE(NEW.trade_intent_id, event_row.trade_intent_id);
                NEW.risk_decision_id := COALESCE(NEW.risk_decision_id, event_row.risk_decision_id);
            END IF;
        END IF;
    END IF;

    NEW.protection_id := COALESCE(NEW.protection_id, NEW.trade_id);
    NEW.entry_fill_ids := COALESCE(
        (
            SELECT JSONB_AGG(fill.fill_id ORDER BY fill.source_time, fill.evaluated_at, fill.fill_id)
              FROM omnix_trading_paper_fills AS fill
             WHERE fill.workspace_id = NEW.workspace_id
               AND fill.account_id = NEW.account_id
               AND fill.order_id = NEW.entry_order_id
        ),
        '[]'::JSONB
    );
    NEW.exit_fill_ids := COALESCE(
        (
            SELECT JSONB_AGG(fill.fill_id ORDER BY fill.source_time, fill.evaluated_at, fill.fill_id)
              FROM omnix_trading_paper_fills AS fill
             WHERE fill.workspace_id = NEW.workspace_id
               AND fill.account_id = NEW.account_id
               AND fill.order_id = NEW.exit_order_id
        ),
        '[]'::JSONB
    );
    NEW.lifecycle_state := CASE
        WHEN NEW.exit_order_id IS NOT NULL AND NEW.exit_time IS NOT NULL THEN 'closed'
        WHEN NEW.entry_order_id IS NOT NULL AND NEW.entry_time IS NOT NULL THEN 'open'
        ELSE COALESCE(NEW.lifecycle_state, 'intent')
    END;
    NEW.review_state := COALESCE(NEW.review_state, 'pending');
    RETURN NEW;
END;
$$;

-- Re-run existing canonical trades through the upgraded correlator. Exact
-- entry_signal_event_id joins preserve one-to-one trade evidence.
UPDATE omnix_trading_paper_trade_records
   SET entry_signal_event_id = entry_signal_event_id;
