-- Rename the managed Finviz strategy to its durable interday group identity
-- and attach the two existing deterministic configurations as child arms.

ALTER TABLE omnix_trading_strategy_configs
    ADD COLUMN IF NOT EXISTS parent_strategy_id TEXT;

CREATE INDEX IF NOT EXISTS idx_omnix_trading_strategy_configs_parent
    ON omnix_trading_strategy_configs (workspace_id, parent_strategy_id, updated_at DESC);

-- The strategy identity is part of the composite key used by the three
-- operational evidence tables. Enable key propagation for this one-time
-- identity migration while retaining the existing delete behavior.
ALTER TABLE omnix_trading_strategy_runs
    DROP CONSTRAINT IF EXISTS omnix_trading_strategy_runs_workspace_id_strategy_id_fkey;
ALTER TABLE omnix_trading_strategy_runs
    ADD CONSTRAINT omnix_trading_strategy_runs_workspace_id_strategy_id_fkey
    FOREIGN KEY (workspace_id, strategy_id)
    REFERENCES omnix_trading_strategy_configs(workspace_id, strategy_id)
    ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE omnix_trading_strategy_events
    DROP CONSTRAINT IF EXISTS omnix_trading_strategy_events_workspace_id_strategy_id_fkey;
ALTER TABLE omnix_trading_strategy_events
    ADD CONSTRAINT omnix_trading_strategy_events_workspace_id_strategy_id_fkey
    FOREIGN KEY (workspace_id, strategy_id)
    REFERENCES omnix_trading_strategy_configs(workspace_id, strategy_id)
    ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE omnix_trading_strategy_protections
    DROP CONSTRAINT IF EXISTS omnix_trading_strategy_protections_workspace_id_strategy_id_fkey;
ALTER TABLE omnix_trading_strategy_protections
    ADD CONSTRAINT omnix_trading_strategy_protections_workspace_id_strategy_id_fkey
    FOREIGN KEY (workspace_id, strategy_id)
    REFERENCES omnix_trading_strategy_configs(workspace_id, strategy_id)
    ON UPDATE CASCADE ON DELETE CASCADE;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM omnix_trading_strategy_configs
         WHERE strategy_id = 'finviz-learning-v2-shadow'
    ) THEN
        IF EXISTS (
            SELECT 1
              FROM omnix_trading_strategy_configs
             WHERE strategy_id = 'interday-trading-strategy-shadow'
        ) THEN
            RAISE EXCEPTION
                'cannot rename finviz-learning-v2-shadow: interday-trading-strategy-shadow already exists';
        END IF;

        UPDATE omnix_trading_strategy_configs
           SET strategy_id = 'interday-trading-strategy-shadow'
         WHERE strategy_id = 'finviz-learning-v2-shadow';
    END IF;
END
$$;

-- These tables retain strategy ownership as a reporting dimension but do not
-- have a config FK. Move mutable operational/reporting associations with the
-- renamed strategy. Research records intentionally remain immutable under
-- their original strategy identity and fingerprint.
UPDATE omnix_trading_backtest_runs
   SET strategy_id = 'interday-trading-strategy-shadow'
 WHERE strategy_id = 'finviz-learning-v2-shadow';

UPDATE omnix_trading_paper_trade_records
   SET strategy_id = 'interday-trading-strategy-shadow'
 WHERE strategy_id = 'finviz-learning-v2-shadow';

UPDATE omnix_trading_strategy_archives
   SET strategy_id = 'interday-trading-strategy-shadow'
 WHERE strategy_id = 'finviz-learning-v2-shadow';

UPDATE omnix_trading_strategy_configs
   SET parent_strategy_id = 'interday-trading-strategy-shadow'
 WHERE strategy_id IN (
       'stoch-rsi-5min',
       'gap-pullback-v2-prospective-20260825'
   )
   AND EXISTS (
       SELECT 1
         FROM omnix_trading_strategy_configs AS parent
        WHERE parent.workspace_id = omnix_trading_strategy_configs.workspace_id
          AND parent.strategy_id = 'interday-trading-strategy-shadow'
   );

ALTER TABLE omnix_trading_strategy_configs
    DROP CONSTRAINT IF EXISTS omnix_trading_strategy_configs_parent_strategy_fkey;
ALTER TABLE omnix_trading_strategy_configs
    ADD CONSTRAINT omnix_trading_strategy_configs_parent_strategy_fkey
    FOREIGN KEY (workspace_id, parent_strategy_id)
    REFERENCES omnix_trading_strategy_configs(workspace_id, strategy_id)
    ON UPDATE CASCADE ON DELETE SET NULL;
