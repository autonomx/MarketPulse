-- Preserve the strategy-event domain payload boundary.
--
-- 0045/0046 introduced first-class lifecycle correlation columns, but their
-- BEFORE INSERT trigger also mirrored those envelope values into the JSONB
-- payload. That made strict typed payloads (notably DynamicCandidate /
-- CompleteDynamicCandidate) fail validation after a persistence round trip and
-- could overwrite legitimate domain fields that happened to share names such
-- as setup_id.
--
-- Correlation authority lives in the dedicated strategy-event columns. Keep
-- the existing v1/v2 identity derivation and legacy input fallbacks, but never
-- mutate the domain payload. Historical rows are intentionally not rewritten:
-- their original same-named domain values cannot always be reconstructed, and
-- typed readers handle the known legacy envelope keys compatibly.

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

    -- Transitional compatibility: older writers put the causal attempt id in
    -- the typed payload. New writers may populate the dedicated column.
    payload_attempt := NULLIF(NEW.payload ->> 'trade_attempt_id', '');
    NEW.trade_attempt_id := COALESCE(NEW.trade_attempt_id, payload_attempt);
    NEW.correlation_version := CASE
        WHEN NEW.trade_attempt_id IS NOT NULL THEN 'trade-lifecycle-v2'
        ELSE COALESCE(NEW.correlation_version, 'trade-lifecycle-v1')
    END;
    NEW.session_id := COALESCE(
        NEW.session_id,
        'session-' || SUBSTRING(
            MD5(
                NEW.workspace_id || '|' || NEW.strategy_id || '|'
                || session_date_value::TEXT
            ),
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
                    NEW.workspace_id || '|' || NEW.strategy_id || '|'
                    || session_date_value::TEXT || '|' || NEW.instrument_id
                ),
                1,
                24
            )
        );
        NEW.trade_intent_id := COALESCE(
            NEW.trade_intent_id,
            'intent-' || SUBSTRING(
                MD5(
                    NEW.workspace_id || '|' || NEW.strategy_id || '|'
                    || session_date_value::TEXT || '|' || NEW.instrument_id
                    || '|long-entry'
                ),
                1,
                24
            )
        );
    END IF;

    -- Risk evidence remains a domain payload member. Derive the envelope id
    -- from it without copying the derived id back into the payload.
    risk_payload := NEW.payload -> 'risk_decision';
    IF risk_payload IS NOT NULL AND risk_payload <> 'null'::JSONB THEN
        NEW.risk_decision_id := 'risk-' || SUBSTRING(
            MD5(NEW.trade_intent_id || '|' || risk_payload::TEXT),
            1,
            24
        );
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION omnix_trading_stamp_strategy_event_correlation() IS
    'Stamps lifecycle correlation columns without mutating typed strategy-event payloads.';
