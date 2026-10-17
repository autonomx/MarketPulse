-- Retire the obsolete Yahoo gap-pullback strategy instance that was superseded
-- by the managed Finviz V2 strategy. Preserve all historical evidence by using
-- the same soft-archive state as TradingStrategyRepository.delete_config().
--
-- Safety boundary: never retire AUTO PAPER and never retire while an active
-- protection exists. Either condition requires explicit operator handling so an
-- in-flight paper position cannot lose its protection monitor as a side effect
-- of this cleanup migration.

UPDATE omnix_trading_strategy_configs AS config
   SET mode = 'off',
       enabled = FALSE,
       archived_at = CURRENT_TIMESTAMP,
       archived_reason = 'operator_archive',
       revision = revision + 1,
       updated_at = CURRENT_TIMESTAMP
 WHERE config.strategy_id = 'gap-pullback-1787099664227'
   AND config.archived_at IS NULL
   AND config.mode <> 'auto_paper'
   AND NOT EXISTS (
       SELECT 1
         FROM omnix_trading_strategy_protections AS protection
        WHERE protection.workspace_id = config.workspace_id
          AND protection.strategy_id = config.strategy_id
          AND protection.status IN ('pending_entry', 'active', 'exit_submitted')
   );
