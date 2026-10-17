-- Preserve deterministic execution spread authority on durable AI-v3 trigger plans.
-- 0083 created the table before max_spread_bps became a required TriggerPlan field.

ALTER TABLE omnix_trading_trigger_plans
    ADD COLUMN IF NOT EXISTS max_spread_bps NUMERIC(20, 8);

UPDATE omnix_trading_trigger_plans
   SET max_spread_bps = 300
 WHERE max_spread_bps IS NULL;

ALTER TABLE omnix_trading_trigger_plans
    ALTER COLUMN max_spread_bps SET DEFAULT 300,
    ALTER COLUMN max_spread_bps SET NOT NULL;

ALTER TABLE omnix_trading_trigger_plans
    DROP CONSTRAINT IF EXISTS omnix_trading_trigger_plans_max_spread_bps_check;

ALTER TABLE omnix_trading_trigger_plans
    ADD CONSTRAINT omnix_trading_trigger_plans_max_spread_bps_check
    CHECK (max_spread_bps > 0);
