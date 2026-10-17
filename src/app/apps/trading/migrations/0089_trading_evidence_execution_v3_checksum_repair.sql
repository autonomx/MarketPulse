-- Follow-up for the 0083 trading evidence migration.
--
-- 0083 was edited after it had already been applied in some environments.
-- Keep the edits forward-only and idempotent so those environments can
-- converge without rewriting their durable migration history.

ALTER TABLE omnix_trading_strategy_protections
    DROP CONSTRAINT IF EXISTS omnix_trading_strategy_protections_status_check;
ALTER TABLE omnix_trading_strategy_protections
    ADD CONSTRAINT omnix_trading_strategy_protections_status_check
    CHECK (status IN (
        'pending_entry', 'active', 'exit_submitted',
        'closed', 'cancelled', 'quarantined'
    ));

-- IBKR market-data bindings were omitted from the original 0083 backfill.
-- Update the purpose and quarantine active protections in one statement so
-- the execution-binding check remains valid throughout the transition.
UPDATE omnix_trading_paper_protections
   SET binding_purpose = 'LIVE_DATA',
       status = CASE
           WHEN status IN ('pending_entry', 'active', 'exit_submitted')
               THEN 'quarantined'
           ELSE status
       END,
       trigger_reason = CASE
           WHEN status IN ('pending_entry', 'active', 'exit_submitted')
               THEN 'binding_purpose_not_execution'
           ELSE trigger_reason
       END,
       revision = CASE
           WHEN status IN ('pending_entry', 'active', 'exit_submitted')
               THEN revision + 1
           ELSE revision
       END,
       updated_at = CASE
           WHEN status IN ('pending_entry', 'active', 'exit_submitted')
               THEN CURRENT_TIMESTAMP
           ELSE updated_at
       END
 WHERE lower(COALESCE(binding_id, '')) LIKE 'ibkr:%';
