-- omnix-migration: phase=contract transactional=true
-- Attach the single-trade early-session 5m Stoch RSI variant as a live SHADOW
-- child of the interday group. The child is cloned from the workspace's
-- existing ``stoch-rsi-5min`` child so it shares its account, universe filters
-- and indicator settings; only ``trade_selection`` differs. It consumes its
-- own daily auto-archived universe rather than any pinned explicit universe.
--
-- Contract: application code older than this migration rejects the new
-- ``trade_selection`` config field.

INSERT INTO omnix_trading_strategy_configs (
    workspace_id, strategy_id, parent_strategy_id, account_id, owner_user_id,
    strategy_kind, strategy_version, mode, active_universe_id,
    config, risk, enabled
)
SELECT source.workspace_id,
       'stoch-rsi-5min-early-single',
       'interday-trading-strategy-shadow',
       source.account_id,
       source.owner_user_id,
       source.strategy_kind,
       source.strategy_version,
       'shadow',
       NULL,
       source.config
           || '{"trade_selection": "early_single", "auto_archive_daily_universe": true}'::jsonb,
       source.risk,
       TRUE
  FROM omnix_trading_strategy_configs AS source
 WHERE source.strategy_id = 'stoch-rsi-5min'
   AND source.strategy_kind = 'stoch_rsi_5m_v1'
   AND source.archived_at IS NULL
   AND EXISTS (
       SELECT 1
         FROM omnix_trading_strategy_configs AS parent
        WHERE parent.workspace_id = source.workspace_id
          AND parent.strategy_id = 'interday-trading-strategy-shadow'
   )
ON CONFLICT (workspace_id, strategy_id) DO NOTHING;
