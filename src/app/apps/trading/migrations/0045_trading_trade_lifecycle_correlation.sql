-- Canonical trade lifecycle correlation.
--
-- Strategy events remain the immutable decision/evidence stream. Completed
-- AUTO PAPER trades remain materialized in omnix_trading_paper_trade_records.
-- This migration gives both layers deterministic correlation identities so a
-- later Command Center / journal can traverse one trade from setup through
-- risk, orders, fills, protection, exit, and review without inventing a second
-- trade database.

ALTER TABLE omnix_trading_strategy_events
    ADD COLUMN IF NOT EXISTS correlation_version TEXT;
ALTER TABLE omnix_trading_strategy_events
    ADD COLUMN IF NOT EXISTS strategy_revision BIGINT;
ALTER TABLE omnix_trading_strategy_events
    ADD COLUMN IF NOT EXISTS session_id TEXT;
ALTER TABLE omnix_trading_strategy_events
    ADD COLUMN IF NOT EXISTS setup_id TEXT;
ALTER TABLE omnix_trading_strategy_events
    ADD COLUMN IF NOT EXISTS trade_intent_id TEXT;
ALTER TABLE omnix_trading_strategy_events
    ADD COLUMN IF NOT EXISTS risk_decision_id TEXT;

CREATE INDEX IF NOT EXISTS idx_omnix_trading_strategy_events_correlation
    ON omnix_trading_strategy_events (
        workspace_id, strategy_id, session_id, setup_id, trade_intent_id, observed_at
    );
CREATE INDEX IF NOT EXISTS idx_omnix_trading_strategy_events_risk_decision
    ON omnix_trading_strategy_events (workspace_id, strategy_id, risk_decision_id)
    WHERE risk_decision_id IS NOT NULL;

CREATE OR REPLACE FUNCTION omnix_trading_stamp_strategy_event_correlation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    session_date_value DATE;
    current_revision BIGINT;
    risk_payload JSONB;
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

    NEW.correlation_version := COALESCE(NEW.correlation_version, 'trade-lifecycle-v1');
    NEW.session_id := COALESCE(
        NEW.session_id,
        'session-' || SUBSTRING(
            MD5(NEW.workspace_id || '|' || NEW.strategy_id || '|' || session_date_value::TEXT),
            1,
            24
        )
    );
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

    risk_payload := NEW.payload -> 'risk_decision';
    IF NEW.risk_decision_id IS NULL AND risk_payload IS NOT NULL AND risk_payload <> 'null'::JSONB THEN
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
                'trade_intent_id', NEW.trade_intent_id,
                'risk_decision_id', NEW.risk_decision_id
            )
        );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_omnix_trading_strategy_event_correlation
    ON omnix_trading_strategy_events;
CREATE TRIGGER trg_omnix_trading_strategy_event_correlation
BEFORE INSERT ON omnix_trading_strategy_events
FOR EACH ROW EXECUTE FUNCTION omnix_trading_stamp_strategy_event_correlation();

-- Existing event rows predate revision stamping. Their deterministic session /
-- setup / intent identities can be reconstructed exactly, but the historical
-- config revision cannot be inferred safely after later edits, so it remains
-- NULL rather than being fabricated from the current config revision.
UPDATE omnix_trading_strategy_events
   SET correlation_version = COALESCE(correlation_version, 'trade-lifecycle-v1'),
       session_id = COALESCE(
           session_id,
           'session-' || SUBSTRING(
               MD5(
                   workspace_id || '|' || strategy_id || '|'
                   || ((observed_at AT TIME ZONE 'America/New_York')::DATE)::TEXT
               ),
               1,
               24
           )
       ),
       setup_id = COALESCE(
           setup_id,
           'setup-' || SUBSTRING(
               MD5(
                   workspace_id || '|' || strategy_id || '|'
                   || ((observed_at AT TIME ZONE 'America/New_York')::DATE)::TEXT
                   || '|' || instrument_id
               ),
               1,
               24
           )
       ),
       trade_intent_id = COALESCE(
           trade_intent_id,
           'intent-' || SUBSTRING(
               MD5(
                   workspace_id || '|' || strategy_id || '|'
                   || ((observed_at AT TIME ZONE 'America/New_York')::DATE)::TEXT
                   || '|' || instrument_id || '|long-entry'
               ),
               1,
               24
           )
       );

UPDATE omnix_trading_strategy_events
   SET risk_decision_id = 'risk-' || SUBSTRING(
           MD5(trade_intent_id || '|' || (payload -> 'risk_decision')::TEXT),
           1,
           24
       )
 WHERE risk_decision_id IS NULL
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
               'trade_intent_id', trade_intent_id,
               'risk_decision_id', risk_decision_id
           )
       );

ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS correlation_version TEXT NOT NULL DEFAULT 'trade-lifecycle-v1';
ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS strategy_revision BIGINT;
ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS strategy_run_id TEXT;
ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS session_id TEXT;
ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS setup_id TEXT;
ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS trade_intent_id TEXT;
ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS risk_decision_id TEXT;
ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS protection_id TEXT;
ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS entry_fill_ids JSONB NOT NULL DEFAULT '[]'::JSONB;
ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS exit_fill_ids JSONB NOT NULL DEFAULT '[]'::JSONB;
ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS lifecycle_state TEXT NOT NULL DEFAULT 'closed';
ALTER TABLE omnix_trading_paper_trade_records
    ADD COLUMN IF NOT EXISTS review_state TEXT NOT NULL DEFAULT 'pending';

CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_trade_records_correlation
    ON omnix_trading_paper_trade_records (
        workspace_id, account_id, session_id, setup_id, trade_intent_id, entry_time
    );
CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_trade_records_risk_decision
    ON omnix_trading_paper_trade_records (workspace_id, risk_decision_id)
    WHERE risk_decision_id IS NOT NULL;

CREATE OR REPLACE FUNCTION omnix_trading_correlate_paper_trade_record()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    event_row RECORD;
BEGIN
    IF NEW.entry_signal_event_id IS NOT NULL THEN
        SELECT run_id, strategy_revision, correlation_version, session_id,
               setup_id, trade_intent_id, risk_decision_id
          INTO event_row
          FROM omnix_trading_strategy_events
         WHERE workspace_id = NEW.workspace_id
           AND strategy_id = NEW.strategy_id
           AND event_id = NEW.entry_signal_event_id
         LIMIT 1;

        IF FOUND THEN
            NEW.strategy_run_id := COALESCE(NEW.strategy_run_id, event_row.run_id);
            NEW.strategy_revision := COALESCE(NEW.strategy_revision, event_row.strategy_revision);
            NEW.correlation_version := COALESCE(
                event_row.correlation_version,
                NEW.correlation_version,
                'trade-lifecycle-v1'
            );
            NEW.session_id := COALESCE(NEW.session_id, event_row.session_id);
            NEW.setup_id := COALESCE(NEW.setup_id, event_row.setup_id);
            NEW.trade_intent_id := COALESCE(NEW.trade_intent_id, event_row.trade_intent_id);
            NEW.risk_decision_id := COALESCE(NEW.risk_decision_id, event_row.risk_decision_id);
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

DROP TRIGGER IF EXISTS trg_omnix_trading_paper_trade_correlation
    ON omnix_trading_paper_trade_records;
CREATE TRIGGER trg_omnix_trading_paper_trade_correlation
BEFORE INSERT OR UPDATE OF entry_signal_event_id, entry_order_id, exit_order_id, entry_time, exit_time
ON omnix_trading_paper_trade_records
FOR EACH ROW EXECUTE FUNCTION omnix_trading_correlate_paper_trade_record();

-- Run all existing canonical trades through the correlator. The assignment is
-- intentionally idempotent; the BEFORE UPDATE trigger fills only missing
-- identities and refreshes exact fill-id arrays.
UPDATE omnix_trading_paper_trade_records
   SET entry_signal_event_id = entry_signal_event_id;
