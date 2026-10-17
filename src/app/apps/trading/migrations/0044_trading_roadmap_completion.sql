-- Close roadmap correctness gaps discovered during the final trading audit.

ALTER TABLE omnix_trading_strategy_configs
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
ALTER TABLE omnix_trading_strategy_configs
    ADD COLUMN IF NOT EXISTS archived_reason TEXT;
CREATE INDEX IF NOT EXISTS idx_omnix_trading_strategy_configs_archive
    ON omnix_trading_strategy_configs (workspace_id, archived_at, updated_at DESC);

ALTER TABLE omnix_trading_strategy_protections
    ADD COLUMN IF NOT EXISTS initial_stop_price NUMERIC;
ALTER TABLE omnix_trading_strategy_protections
    ADD COLUMN IF NOT EXISTS initial_target_price NUMERIC;
ALTER TABLE omnix_trading_strategy_protections
    ADD COLUMN IF NOT EXISTS mae_price NUMERIC;
ALTER TABLE omnix_trading_strategy_protections
    ADD COLUMN IF NOT EXISTS mfe_price NUMERIC;

UPDATE omnix_trading_strategy_protections AS protection
   SET initial_stop_price = COALESCE(
           protection.initial_stop_price,
           (SELECT NULLIF(event.payload #>> '{signal,stop_price}', '')::NUMERIC
              FROM omnix_trading_strategy_events AS event
             WHERE event.workspace_id = protection.workspace_id
               AND event.strategy_id = protection.strategy_id
               AND event.event_type = 'entry_order_submitted'
               AND event.payload ->> 'order_id' = protection.entry_order_id
             ORDER BY event.observed_at DESC
             LIMIT 1),
           protection.stop_price
       ),
       initial_target_price = COALESCE(
           protection.initial_target_price,
           (SELECT NULLIF(event.payload #>> '{signal,target_price}', '')::NUMERIC
              FROM omnix_trading_strategy_events AS event
             WHERE event.workspace_id = protection.workspace_id
               AND event.strategy_id = protection.strategy_id
               AND event.event_type = 'entry_order_submitted'
               AND event.payload ->> 'order_id' = protection.entry_order_id
             ORDER BY event.observed_at DESC
             LIMIT 1),
           protection.target_price
       )
 WHERE protection.initial_stop_price IS NULL
    OR protection.initial_target_price IS NULL;

UPDATE omnix_trading_strategy_protections AS protection
   SET mae_price = COALESCE(protection.mae_price, entry_order.average_fill_price),
       mfe_price = COALESCE(protection.mfe_price, entry_order.average_fill_price)
  FROM omnix_trading_paper_orders AS entry_order
 WHERE entry_order.workspace_id = protection.workspace_id
   AND entry_order.account_id = protection.account_id
   AND entry_order.order_id = protection.entry_order_id
   AND entry_order.average_fill_price IS NOT NULL
   AND (protection.mae_price IS NULL OR protection.mfe_price IS NULL);

-- Repair canonical trades materialized before initial risk became immutable.
UPDATE omnix_trading_paper_trade_records AS trade
   SET initial_stop = COALESCE(NULLIF(event.payload #>> '{signal,stop_price}', '')::NUMERIC, trade.initial_stop),
       initial_target = COALESCE(NULLIF(event.payload #>> '{signal,target_price}', '')::NUMERIC, trade.initial_target),
       initial_risk_dollars = CASE
           WHEN COALESCE(NULLIF(event.payload #>> '{signal,stop_price}', '')::NUMERIC, trade.initial_stop) IS NULL
           THEN trade.initial_risk_dollars
           ELSE ABS(trade.average_entry_price - COALESCE(NULLIF(event.payload #>> '{signal,stop_price}', '')::NUMERIC, trade.initial_stop)) * trade.quantity
       END,
       realized_r = CASE
           WHEN ABS(trade.average_entry_price - COALESCE(NULLIF(event.payload #>> '{signal,stop_price}', '')::NUMERIC, trade.initial_stop)) * trade.quantity > 0
           THEN trade.realized_pnl / (ABS(trade.average_entry_price - COALESCE(NULLIF(event.payload #>> '{signal,stop_price}', '')::NUMERIC, trade.initial_stop)) * trade.quantity)
           ELSE trade.realized_r
       END,
       updated_at = CURRENT_TIMESTAMP
  FROM omnix_trading_strategy_events AS event
 WHERE event.workspace_id = trade.workspace_id
   AND event.strategy_id = trade.strategy_id
   AND event.event_id = trade.entry_signal_event_id;

CREATE OR REPLACE FUNCTION omnix_trading_refresh_strategy_trade_metrics()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    entry_price NUMERIC;
    risk_per_share NUMERIC;
    trade_pnl NUMERIC;
BEGIN
    IF NEW.status <> 'closed' OR NEW.exit_order_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT average_fill_price INTO entry_price
      FROM omnix_trading_paper_orders
     WHERE workspace_id = NEW.workspace_id
       AND account_id = NEW.account_id
       AND order_id = NEW.entry_order_id
     LIMIT 1;
    IF entry_price IS NULL THEN
        RETURN NEW;
    END IF;

    risk_per_share := ABS(entry_price - COALESCE(NEW.initial_stop_price, NEW.stop_price));
    SELECT realized_pnl INTO trade_pnl
      FROM omnix_trading_paper_trade_records
     WHERE workspace_id = NEW.workspace_id
       AND account_id = NEW.account_id
       AND trade_id = NEW.protection_id;

    UPDATE omnix_trading_paper_trade_records
       SET initial_stop = COALESCE(NEW.initial_stop_price, initial_stop, NEW.stop_price),
           initial_target = COALESCE(NEW.initial_target_price, initial_target, NEW.target_price),
           initial_risk_dollars = CASE WHEN risk_per_share > 0 THEN risk_per_share * quantity ELSE initial_risk_dollars END,
           realized_r = CASE WHEN risk_per_share > 0 THEN realized_pnl / (risk_per_share * quantity) ELSE realized_r END,
           mae_r = CASE WHEN risk_per_share > 0 AND NEW.mae_price IS NOT NULL THEN (NEW.mae_price - entry_price) / risk_per_share ELSE mae_r END,
           mfe_r = CASE WHEN risk_per_share > 0 AND NEW.mfe_price IS NOT NULL THEN (NEW.mfe_price - entry_price) / risk_per_share ELSE mfe_r END,
           updated_at = CURRENT_TIMESTAMP
     WHERE workspace_id = NEW.workspace_id
       AND account_id = NEW.account_id
       AND trade_id = NEW.protection_id;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_zz_omnix_trading_strategy_trade_metrics ON omnix_trading_strategy_protections;
CREATE TRIGGER trg_zz_omnix_trading_strategy_trade_metrics
AFTER INSERT OR UPDATE ON omnix_trading_strategy_protections
FOR EACH ROW EXECUTE FUNCTION omnix_trading_refresh_strategy_trade_metrics();

-- Recreate equity snapshots so protected stops above entry contribute zero
-- remaining downside risk rather than being counted via ABS(distance).
CREATE OR REPLACE FUNCTION omnix_trading_capture_paper_equity_snapshot()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    target_workspace TEXT;
    target_account TEXT;
    target_epoch TEXT;
    current_cash NUMERIC := 0;
    current_reserved NUMERIC := 0;
    market_value NUMERIC := 0;
    realized_value NUMERIC := 0;
    unrealized_value NUMERIC := 0;
    exposure_value NUMERIC := 0;
    strategy_risk NUMERIC := 0;
    manual_risk NUMERIC := 0;
BEGIN
    target_workspace := NEW.workspace_id;
    target_account := NEW.account_id;
    SELECT epoch_id INTO target_epoch
      FROM omnix_trading_paper_simulation_epochs
     WHERE workspace_id = target_workspace AND account_id = target_account AND is_current
     ORDER BY ordinal DESC LIMIT 1;
    IF target_epoch IS NULL THEN RETURN NEW; END IF;

    SELECT COALESCE(SUM(available + reserved), 0), COALESCE(SUM(reserved), 0)
      INTO current_cash, current_reserved
      FROM omnix_trading_paper_balances
     WHERE workspace_id = target_workspace AND account_id = target_account;

    SELECT COALESCE(SUM(quantity * COALESCE(last_price, average_cost, 0)), 0),
           COALESCE(SUM(realized_pnl), 0), COALESCE(SUM(unrealized_pnl), 0),
           COALESCE(SUM(ABS(quantity * COALESCE(last_price, average_cost, 0))), 0)
      INTO market_value, realized_value, unrealized_value, exposure_value
      FROM omnix_trading_paper_positions
     WHERE workspace_id = target_workspace AND account_id = target_account;

    SELECT COALESCE(SUM(
               GREATEST(0, COALESCE(ord.average_fill_price, ord.reference_price, ord.limit_price, ord.stop_price, protection.stop_price)
                   - protection.stop_price) * protection.quantity
           ), 0)
      INTO strategy_risk
      FROM omnix_trading_strategy_protections AS protection
      LEFT JOIN omnix_trading_paper_orders AS ord
        ON ord.workspace_id = protection.workspace_id
       AND ord.account_id = protection.account_id
       AND ord.order_id = protection.entry_order_id
     WHERE protection.workspace_id = target_workspace
       AND protection.account_id = target_account
       AND protection.status IN ('pending_entry', 'active', 'exit_submitted');

    SELECT COALESCE(SUM(
               GREATEST(0, position.average_cost - protection.stop_loss) * ABS(position.quantity)
           ), 0)
      INTO manual_risk
      FROM omnix_trading_paper_protections AS protection
      JOIN omnix_trading_paper_positions AS position
        ON position.workspace_id = protection.workspace_id
       AND position.account_id = protection.account_id
       AND position.instrument_id = protection.instrument_id
     WHERE protection.workspace_id = target_workspace
       AND protection.account_id = target_account
       AND protection.status IN ('pending_entry', 'active', 'exit_submitted')
       AND protection.stop_loss IS NOT NULL;

    INSERT INTO omnix_trading_paper_equity_snapshots (
        workspace_id, account_id, epoch_id, cash, reserved_cash, equity,
        realized_pnl, unrealized_pnl, gross_exposure, risk_at_stop, source
    ) VALUES (
        target_workspace, target_account, target_epoch, current_cash, current_reserved,
        current_cash + market_value, realized_value, unrealized_value, exposure_value,
        strategy_risk + manual_risk, TG_TABLE_NAME
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_omnix_trading_strategy_protection_equity ON omnix_trading_strategy_protections;
CREATE TRIGGER trg_omnix_trading_strategy_protection_equity
AFTER INSERT OR UPDATE ON omnix_trading_strategy_protections
FOR EACH ROW EXECUTE FUNCTION omnix_trading_capture_paper_equity_snapshot();

DROP TRIGGER IF EXISTS trg_omnix_trading_manual_protection_equity ON omnix_trading_paper_protections;
CREATE TRIGGER trg_omnix_trading_manual_protection_equity
AFTER INSERT OR UPDATE ON omnix_trading_paper_protections
FOR EACH ROW EXECUTE FUNCTION omnix_trading_capture_paper_equity_snapshot();
