CREATE OR REPLACE FUNCTION omnix_preserve_trading_alert_lifecycle_history()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.instrument_id IS NOT DISTINCT FROM OLD.instrument_id
       AND NEW.binding_id IS NOT DISTINCT FROM OLD.binding_id
       AND NEW.condition_type IS NOT DISTINCT FROM OLD.condition_type
       AND NEW.threshold IS NOT DISTINCT FROM OLD.threshold
       AND NEW.condition_parameters IS NOT DISTINCT FROM OLD.condition_parameters
       AND NEW.evaluation_policy IS NOT DISTINCT FROM OLD.evaluation_policy
    THEN
        NEW.last_observed_price := OLD.last_observed_price;
        NEW.last_observed_value := OLD.last_observed_value;
        NEW.last_triggered_at := OLD.last_triggered_at;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_omnix_trading_alert_lifecycle_history
    ON omnix_trading_alerts;

CREATE TRIGGER trg_omnix_trading_alert_lifecycle_history
BEFORE UPDATE ON omnix_trading_alerts
FOR EACH ROW
EXECUTE FUNCTION omnix_preserve_trading_alert_lifecycle_history();
