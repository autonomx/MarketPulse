-- Durable paper-trading analytics, simulation epochs, and audit preservation.
--
-- Operational paper tables intentionally remain optimized for the current
-- simulation. Resets archive the current operational state, close its epoch,
-- and start a fresh epoch. Analytics tables survive resets and strategy
-- deletion so prospective evidence cannot be erased accidentally.

CREATE TABLE IF NOT EXISTS omnix_trading_paper_simulation_epochs (
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    epoch_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    initial_cash NUMERIC NOT NULL DEFAULT 0 CHECK (initial_cash >= 0),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMPTZ,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    end_reason TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (workspace_id, account_id, epoch_id),
    UNIQUE (workspace_id, account_id, ordinal),
    FOREIGN KEY (workspace_id, account_id)
        REFERENCES omnix_trading_paper_accounts(workspace_id, account_id)
        ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_omnix_trading_paper_epoch_current
    ON omnix_trading_paper_simulation_epochs (workspace_id, account_id)
    WHERE is_current;

CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_epochs_recent
    ON omnix_trading_paper_simulation_epochs (workspace_id, account_id, started_at DESC);

INSERT INTO omnix_trading_paper_simulation_epochs (
    workspace_id, account_id, epoch_id, ordinal, initial_cash, started_at, is_current, metadata
)
SELECT account.workspace_id,
       account.account_id,
       'epoch-0001',
       1,
       COALESCE((
           SELECT SUM(balance.available + balance.reserved)
             FROM omnix_trading_paper_balances AS balance
            WHERE balance.workspace_id = account.workspace_id
              AND balance.account_id = account.account_id
       ), 0),
       account.created_at,
       TRUE,
       jsonb_build_object('source', 'analytics_migration_seed')
  FROM omnix_trading_paper_accounts AS account
 WHERE NOT EXISTS (
       SELECT 1
         FROM omnix_trading_paper_simulation_epochs AS epoch
        WHERE epoch.workspace_id = account.workspace_id
          AND epoch.account_id = account.account_id
 )
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS omnix_trading_paper_epoch_archives (
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    epoch_id TEXT NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    PRIMARY KEY (workspace_id, account_id, epoch_id),
    FOREIGN KEY (workspace_id, account_id, epoch_id)
        REFERENCES omnix_trading_paper_simulation_epochs(workspace_id, account_id, epoch_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_trading_paper_equity_snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    epoch_id TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cash NUMERIC NOT NULL DEFAULT 0,
    reserved_cash NUMERIC NOT NULL DEFAULT 0,
    equity NUMERIC NOT NULL DEFAULT 0,
    realized_pnl NUMERIC NOT NULL DEFAULT 0,
    unrealized_pnl NUMERIC NOT NULL DEFAULT 0,
    gross_exposure NUMERIC NOT NULL DEFAULT 0,
    risk_at_stop NUMERIC NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'paper_mutation',
    FOREIGN KEY (workspace_id, account_id, epoch_id)
        REFERENCES omnix_trading_paper_simulation_epochs(workspace_id, account_id, epoch_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_equity_history
    ON omnix_trading_paper_equity_snapshots (workspace_id, account_id, epoch_id, observed_at);

CREATE TABLE IF NOT EXISTS omnix_trading_paper_trade_records (
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    epoch_id TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    strategy_id TEXT,
    strategy_version TEXT,
    profile_fingerprint TEXT,
    mode TEXT NOT NULL DEFAULT 'auto_paper',
    universe_id TEXT,
    instrument_id TEXT NOT NULL,
    session_date DATE,
    entry_signal_event_id TEXT,
    entry_order_id TEXT NOT NULL,
    exit_order_id TEXT NOT NULL,
    signal_price NUMERIC,
    executable_entry_price NUMERIC,
    average_entry_price NUMERIC NOT NULL,
    average_exit_price NUMERIC NOT NULL,
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    initial_risk_dollars NUMERIC,
    initial_stop NUMERIC,
    initial_target NUMERIC,
    realized_pnl NUMERIC NOT NULL,
    realized_r NUMERIC,
    mae_r NUMERIC,
    mfe_r NUMERIC,
    signal_to_executable_bps NUMERIC,
    fill_slippage_bps NUMERIC,
    implementation_shortfall_bps NUMERIC,
    entry_time TIMESTAMPTZ NOT NULL,
    exit_time TIMESTAMPTZ NOT NULL,
    holding_seconds INTEGER NOT NULL DEFAULT 0 CHECK (holding_seconds >= 0),
    exit_reason TEXT,
    setup_features JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_features JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, account_id, trade_id),
    FOREIGN KEY (workspace_id, account_id, epoch_id)
        REFERENCES omnix_trading_paper_simulation_epochs(workspace_id, account_id, epoch_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_trade_records_strategy
    ON omnix_trading_paper_trade_records (
        workspace_id, strategy_id, strategy_version, profile_fingerprint, session_date, entry_time
    );

CREATE INDEX IF NOT EXISTS idx_omnix_trading_paper_trade_records_epoch
    ON omnix_trading_paper_trade_records (workspace_id, account_id, epoch_id, entry_time);

-- Archive deleted strategy-owned evidence before the existing ON DELETE CASCADE
-- relationships remove it. This is deliberately independent of the config FK.
CREATE TABLE IF NOT EXISTS omnix_trading_strategy_archives (
    archive_id BIGSERIAL PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason TEXT NOT NULL DEFAULT 'strategy_deleted',
    config_snapshot JSONB NOT NULL,
    runs JSONB NOT NULL DEFAULT '[]'::jsonb,
    events JSONB NOT NULL DEFAULT '[]'::jsonb,
    protections JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_strategy_archives_lookup
    ON omnix_trading_strategy_archives (workspace_id, strategy_id, archived_at DESC);

-- Stamp the current epoch on order/fill/ledger/protection evidence without
-- changing the operational primary keys or repository call sites.
ALTER TABLE omnix_trading_paper_orders ADD COLUMN IF NOT EXISTS epoch_id TEXT;
ALTER TABLE omnix_trading_paper_fills ADD COLUMN IF NOT EXISTS epoch_id TEXT;
ALTER TABLE omnix_trading_paper_ledger ADD COLUMN IF NOT EXISTS epoch_id TEXT;
ALTER TABLE omnix_trading_paper_protections ADD COLUMN IF NOT EXISTS epoch_id TEXT;

UPDATE omnix_trading_paper_orders AS item
   SET epoch_id = epoch.epoch_id
  FROM omnix_trading_paper_simulation_epochs AS epoch
 WHERE item.workspace_id = epoch.workspace_id
   AND item.account_id = epoch.account_id
   AND epoch.is_current
   AND item.epoch_id IS NULL;

UPDATE omnix_trading_paper_fills AS item
   SET epoch_id = epoch.epoch_id
  FROM omnix_trading_paper_simulation_epochs AS epoch
 WHERE item.workspace_id = epoch.workspace_id
   AND item.account_id = epoch.account_id
   AND epoch.is_current
   AND item.epoch_id IS NULL;

UPDATE omnix_trading_paper_ledger AS item
   SET epoch_id = epoch.epoch_id
  FROM omnix_trading_paper_simulation_epochs AS epoch
 WHERE item.workspace_id = epoch.workspace_id
   AND item.account_id = epoch.account_id
   AND epoch.is_current
   AND item.epoch_id IS NULL;

UPDATE omnix_trading_paper_protections AS item
   SET epoch_id = epoch.epoch_id
  FROM omnix_trading_paper_simulation_epochs AS epoch
 WHERE item.workspace_id = epoch.workspace_id
   AND item.account_id = epoch.account_id
   AND epoch.is_current
   AND item.epoch_id IS NULL;

CREATE OR REPLACE FUNCTION omnix_trading_ensure_paper_epoch_for_balance()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    next_ordinal INTEGER;
    next_epoch_id TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM omnix_trading_paper_simulation_epochs
         WHERE workspace_id = NEW.workspace_id
           AND account_id = NEW.account_id
           AND is_current
    ) THEN
        SELECT COALESCE(MAX(ordinal), 0) + 1
          INTO next_ordinal
          FROM omnix_trading_paper_simulation_epochs
         WHERE workspace_id = NEW.workspace_id
           AND account_id = NEW.account_id;
        next_epoch_id := 'epoch-' || LPAD(next_ordinal::TEXT, 4, '0');
        INSERT INTO omnix_trading_paper_simulation_epochs (
            workspace_id, account_id, epoch_id, ordinal, initial_cash, metadata
        ) VALUES (
            NEW.workspace_id,
            NEW.account_id,
            next_epoch_id,
            next_ordinal,
            GREATEST(0, NEW.available + NEW.reserved),
            jsonb_build_object('source', 'balance_insert')
        )
        ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_omnix_trading_paper_balance_epoch ON omnix_trading_paper_balances;
CREATE TRIGGER trg_omnix_trading_paper_balance_epoch
AFTER INSERT ON omnix_trading_paper_balances
FOR EACH ROW EXECUTE FUNCTION omnix_trading_ensure_paper_epoch_for_balance();

CREATE OR REPLACE FUNCTION omnix_trading_stamp_current_paper_epoch()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.epoch_id IS NULL THEN
        SELECT epoch_id
          INTO NEW.epoch_id
          FROM omnix_trading_paper_simulation_epochs
         WHERE workspace_id = NEW.workspace_id
           AND account_id = NEW.account_id
           AND is_current
         ORDER BY ordinal DESC
         LIMIT 1;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_omnix_trading_paper_orders_epoch ON omnix_trading_paper_orders;
CREATE TRIGGER trg_omnix_trading_paper_orders_epoch
BEFORE INSERT ON omnix_trading_paper_orders
FOR EACH ROW EXECUTE FUNCTION omnix_trading_stamp_current_paper_epoch();

DROP TRIGGER IF EXISTS trg_omnix_trading_paper_fills_epoch ON omnix_trading_paper_fills;
CREATE TRIGGER trg_omnix_trading_paper_fills_epoch
BEFORE INSERT ON omnix_trading_paper_fills
FOR EACH ROW EXECUTE FUNCTION omnix_trading_stamp_current_paper_epoch();

DROP TRIGGER IF EXISTS trg_omnix_trading_paper_ledger_epoch ON omnix_trading_paper_ledger;
CREATE TRIGGER trg_omnix_trading_paper_ledger_epoch
BEFORE INSERT ON omnix_trading_paper_ledger
FOR EACH ROW EXECUTE FUNCTION omnix_trading_stamp_current_paper_epoch();

DROP TRIGGER IF EXISTS trg_omnix_trading_paper_protections_epoch ON omnix_trading_paper_protections;
CREATE TRIGGER trg_omnix_trading_paper_protections_epoch
BEFORE INSERT ON omnix_trading_paper_protections
FOR EACH ROW EXECUTE FUNCTION omnix_trading_stamp_current_paper_epoch();

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
     WHERE workspace_id = target_workspace
       AND account_id = target_account
       AND is_current
     ORDER BY ordinal DESC
     LIMIT 1;

    IF target_epoch IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT COALESCE(SUM(available + reserved), 0), COALESCE(SUM(reserved), 0)
      INTO current_cash, current_reserved
      FROM omnix_trading_paper_balances
     WHERE workspace_id = target_workspace
       AND account_id = target_account;

    SELECT COALESCE(SUM(quantity * COALESCE(last_price, average_cost, 0)), 0),
           COALESCE(SUM(realized_pnl), 0),
           COALESCE(SUM(unrealized_pnl), 0),
           COALESCE(SUM(ABS(quantity * COALESCE(last_price, average_cost, 0))), 0)
      INTO market_value, realized_value, unrealized_value, exposure_value
      FROM omnix_trading_paper_positions
     WHERE workspace_id = target_workspace
       AND account_id = target_account;

    SELECT COALESCE(SUM(
               ABS(COALESCE(ord.average_fill_price, ord.reference_price, ord.limit_price, ord.stop_price, protection.stop_price)
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
               ABS(position.average_cost - protection.stop_loss) * ABS(position.quantity)
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
        target_workspace,
        target_account,
        target_epoch,
        current_cash,
        current_reserved,
        current_cash + market_value,
        realized_value,
        unrealized_value,
        exposure_value,
        strategy_risk + manual_risk,
        TG_TABLE_NAME
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_omnix_trading_paper_balance_equity ON omnix_trading_paper_balances;
CREATE TRIGGER trg_omnix_trading_paper_balance_equity
AFTER INSERT OR UPDATE ON omnix_trading_paper_balances
FOR EACH ROW EXECUTE FUNCTION omnix_trading_capture_paper_equity_snapshot();

DROP TRIGGER IF EXISTS trg_omnix_trading_paper_position_equity ON omnix_trading_paper_positions;
CREATE TRIGGER trg_omnix_trading_paper_position_equity
AFTER INSERT OR UPDATE ON omnix_trading_paper_positions
FOR EACH ROW EXECUTE FUNCTION omnix_trading_capture_paper_equity_snapshot();

-- Seed a mark-to-market point for each current simulation so the dashboard has
-- an initial point even before the next paper mutation.
INSERT INTO omnix_trading_paper_equity_snapshots (
    workspace_id, account_id, epoch_id, observed_at, cash, reserved_cash, equity,
    realized_pnl, unrealized_pnl, gross_exposure, risk_at_stop, source
)
SELECT epoch.workspace_id,
       epoch.account_id,
       epoch.epoch_id,
       CURRENT_TIMESTAMP,
       COALESCE((SELECT SUM(balance.available + balance.reserved)
                   FROM omnix_trading_paper_balances AS balance
                  WHERE balance.workspace_id = epoch.workspace_id
                    AND balance.account_id = epoch.account_id), 0),
       COALESCE((SELECT SUM(balance.reserved)
                   FROM omnix_trading_paper_balances AS balance
                  WHERE balance.workspace_id = epoch.workspace_id
                    AND balance.account_id = epoch.account_id), 0),
       COALESCE((SELECT SUM(balance.available + balance.reserved)
                   FROM omnix_trading_paper_balances AS balance
                  WHERE balance.workspace_id = epoch.workspace_id
                    AND balance.account_id = epoch.account_id), 0)
       + COALESCE((SELECT SUM(position.quantity * COALESCE(position.last_price, position.average_cost, 0))
                     FROM omnix_trading_paper_positions AS position
                    WHERE position.workspace_id = epoch.workspace_id
                      AND position.account_id = epoch.account_id), 0),
       COALESCE((SELECT SUM(position.realized_pnl)
                   FROM omnix_trading_paper_positions AS position
                  WHERE position.workspace_id = epoch.workspace_id
                    AND position.account_id = epoch.account_id), 0),
       COALESCE((SELECT SUM(position.unrealized_pnl)
                   FROM omnix_trading_paper_positions AS position
                  WHERE position.workspace_id = epoch.workspace_id
                    AND position.account_id = epoch.account_id), 0),
       COALESCE((SELECT SUM(ABS(position.quantity * COALESCE(position.last_price, position.average_cost, 0)))
                   FROM omnix_trading_paper_positions AS position
                  WHERE position.workspace_id = epoch.workspace_id
                    AND position.account_id = epoch.account_id), 0),
       0,
       'migration_seed'
  FROM omnix_trading_paper_simulation_epochs AS epoch
 WHERE epoch.is_current
   AND NOT EXISTS (
       SELECT 1
         FROM omnix_trading_paper_equity_snapshots AS snapshot
        WHERE snapshot.workspace_id = epoch.workspace_id
          AND snapshot.account_id = epoch.account_id
          AND snapshot.epoch_id = epoch.epoch_id
   );

CREATE OR REPLACE FUNCTION omnix_trading_materialize_strategy_trade()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    current_epoch TEXT;
    entry_row RECORD;
    exit_row RECORD;
    event_row RECORD;
    trade_qty NUMERIC;
    commissions NUMERIC := 0;
    risk_dollars NUMERIC;
    pnl NUMERIC;
    r_value NUMERIC;
    raw_signal_price TEXT;
    raw_executable_price TEXT;
    parsed_signal_price NUMERIC;
    parsed_executable_price NUMERIC;
    signal_exec_bps NUMERIC;
    fill_slip_bps NUMERIC;
    total_shortfall_bps NUMERIC;
    strategy_version_value TEXT;
BEGIN
    IF NEW.status <> 'closed' OR NEW.exit_order_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.status = 'closed' AND OLD.exit_order_id IS NOT DISTINCT FROM NEW.exit_order_id THEN
        RETURN NEW;
    END IF;

    SELECT epoch_id INTO current_epoch
      FROM omnix_trading_paper_simulation_epochs
     WHERE workspace_id = NEW.workspace_id
       AND account_id = NEW.account_id
       AND is_current
     ORDER BY ordinal DESC
     LIMIT 1;
    IF current_epoch IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT order_id, average_fill_price, filled_quantity, quantity,
           COALESCE(updated_at, created_at) AS filled_at
      INTO entry_row
      FROM omnix_trading_paper_orders
     WHERE workspace_id = NEW.workspace_id
       AND account_id = NEW.account_id
       AND order_id = NEW.entry_order_id
     LIMIT 1;

    SELECT order_id, average_fill_price, filled_quantity, quantity,
           COALESCE(updated_at, created_at) AS filled_at
      INTO exit_row
      FROM omnix_trading_paper_orders
     WHERE workspace_id = NEW.workspace_id
       AND account_id = NEW.account_id
       AND order_id = NEW.exit_order_id
     LIMIT 1;

    IF entry_row.average_fill_price IS NULL OR exit_row.average_fill_price IS NULL THEN
        RETURN NEW;
    END IF;

    trade_qty := LEAST(
        NEW.quantity,
        COALESCE(NULLIF(entry_row.filled_quantity, 0), entry_row.quantity, NEW.quantity),
        COALESCE(NULLIF(exit_row.filled_quantity, 0), exit_row.quantity, NEW.quantity)
    );
    IF trade_qty <= 0 THEN
        RETURN NEW;
    END IF;

    SELECT COALESCE(SUM(commission), 0)
      INTO commissions
      FROM omnix_trading_paper_fills
     WHERE workspace_id = NEW.workspace_id
       AND account_id = NEW.account_id
       AND order_id IN (NEW.entry_order_id, NEW.exit_order_id);

    risk_dollars := ABS(entry_row.average_fill_price - NEW.stop_price) * trade_qty;
    pnl := (exit_row.average_fill_price - entry_row.average_fill_price) * trade_qty - commissions;
    r_value := CASE WHEN risk_dollars > 0 THEN pnl / risk_dollars ELSE NULL END;

    SELECT event_id, payload
      INTO event_row
      FROM omnix_trading_strategy_events
     WHERE workspace_id = NEW.workspace_id
       AND strategy_id = NEW.strategy_id
       AND instrument_id = NEW.instrument_id
       AND event_type = 'entry_order_submitted'
       AND payload ->> 'order_id' = NEW.entry_order_id
     ORDER BY observed_at DESC
     LIMIT 1;

    SELECT strategy_version
      INTO strategy_version_value
      FROM omnix_trading_strategy_configs
     WHERE workspace_id = NEW.workspace_id
       AND strategy_id = NEW.strategy_id;

    IF event_row.payload IS NOT NULL THEN
        raw_signal_price := COALESCE(
            event_row.payload #>> '{signal,entry_price}',
            event_row.payload #>> '{signal,breakout_price}',
            event_row.payload #>> '{signal,price}'
        );
        raw_executable_price := COALESCE(
            event_row.payload #>> '{execution,ask}',
            event_row.payload #>> '{execution,last}'
        );
        IF raw_signal_price ~ '^[0-9]+([.][0-9]+)?$' THEN
            parsed_signal_price := raw_signal_price::NUMERIC;
        END IF;
        IF raw_executable_price ~ '^[0-9]+([.][0-9]+)?$' THEN
            parsed_executable_price := raw_executable_price::NUMERIC;
        END IF;
    END IF;

    signal_exec_bps := CASE
        WHEN parsed_signal_price > 0 AND parsed_executable_price IS NOT NULL
        THEN (parsed_executable_price - parsed_signal_price) / parsed_signal_price * 10000
        ELSE NULL
    END;
    fill_slip_bps := CASE
        WHEN parsed_executable_price > 0
        THEN (entry_row.average_fill_price - parsed_executable_price) / parsed_executable_price * 10000
        ELSE NULL
    END;
    total_shortfall_bps := CASE
        WHEN parsed_signal_price > 0
        THEN (entry_row.average_fill_price - parsed_signal_price) / parsed_signal_price * 10000
        ELSE NULL
    END;

    INSERT INTO omnix_trading_paper_trade_records (
        workspace_id, account_id, epoch_id, trade_id, strategy_id, strategy_version,
        profile_fingerprint, mode, universe_id, instrument_id, session_date,
        entry_signal_event_id, entry_order_id, exit_order_id, signal_price,
        executable_entry_price, average_entry_price, average_exit_price, quantity,
        initial_risk_dollars, initial_stop, initial_target, realized_pnl, realized_r,
        signal_to_executable_bps, fill_slippage_bps, implementation_shortfall_bps,
        entry_time, exit_time, holding_seconds, exit_reason, setup_features,
        execution_features
    ) VALUES (
        NEW.workspace_id,
        NEW.account_id,
        current_epoch,
        NEW.protection_id,
        NEW.strategy_id,
        strategy_version_value,
        event_row.payload ->> 'profile_fingerprint',
        'auto_paper',
        event_row.payload ->> 'universe_id',
        NEW.instrument_id,
        (entry_row.filled_at AT TIME ZONE 'America/New_York')::DATE,
        event_row.event_id,
        NEW.entry_order_id,
        NEW.exit_order_id,
        parsed_signal_price,
        parsed_executable_price,
        entry_row.average_fill_price,
        exit_row.average_fill_price,
        trade_qty,
        risk_dollars,
        NEW.stop_price,
        NEW.target_price,
        pnl,
        r_value,
        signal_exec_bps,
        fill_slip_bps,
        total_shortfall_bps,
        entry_row.filled_at,
        exit_row.filled_at,
        GREATEST(0, EXTRACT(EPOCH FROM (exit_row.filled_at - entry_row.filled_at))::INTEGER),
        NEW.trigger_reason,
        COALESCE(event_row.payload -> 'features', '{}'::jsonb),
        COALESCE(event_row.payload -> 'execution', '{}'::jsonb)
    )
    ON CONFLICT (workspace_id, account_id, trade_id) DO UPDATE SET
        exit_order_id = EXCLUDED.exit_order_id,
        average_exit_price = EXCLUDED.average_exit_price,
        quantity = EXCLUDED.quantity,
        realized_pnl = EXCLUDED.realized_pnl,
        realized_r = EXCLUDED.realized_r,
        exit_time = EXCLUDED.exit_time,
        holding_seconds = EXCLUDED.holding_seconds,
        exit_reason = EXCLUDED.exit_reason,
        updated_at = CURRENT_TIMESTAMP;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_omnix_trading_strategy_trade_record ON omnix_trading_strategy_protections;
CREATE TRIGGER trg_omnix_trading_strategy_trade_record
AFTER INSERT OR UPDATE ON omnix_trading_strategy_protections
FOR EACH ROW EXECUTE FUNCTION omnix_trading_materialize_strategy_trade();

CREATE OR REPLACE FUNCTION omnix_trading_archive_strategy_before_delete()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO omnix_trading_strategy_archives (
        workspace_id, strategy_id, account_id, strategy_version, reason,
        config_snapshot, runs, events, protections
    ) VALUES (
        OLD.workspace_id,
        OLD.strategy_id,
        OLD.account_id,
        OLD.strategy_version,
        'strategy_deleted',
        to_jsonb(OLD),
        COALESCE((
            SELECT jsonb_agg(to_jsonb(run) ORDER BY run.started_at)
              FROM omnix_trading_strategy_runs AS run
             WHERE run.workspace_id = OLD.workspace_id
               AND run.strategy_id = OLD.strategy_id
        ), '[]'::jsonb),
        COALESCE((
            SELECT jsonb_agg(to_jsonb(event) ORDER BY event.observed_at, event.event_id)
              FROM omnix_trading_strategy_events AS event
             WHERE event.workspace_id = OLD.workspace_id
               AND event.strategy_id = OLD.strategy_id
        ), '[]'::jsonb),
        COALESCE((
            SELECT jsonb_agg(to_jsonb(protection) ORDER BY protection.created_at, protection.protection_id)
              FROM omnix_trading_strategy_protections AS protection
             WHERE protection.workspace_id = OLD.workspace_id
               AND protection.strategy_id = OLD.strategy_id
        ), '[]'::jsonb)
    );
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_omnix_trading_strategy_archive ON omnix_trading_strategy_configs;
CREATE TRIGGER trg_omnix_trading_strategy_archive
BEFORE DELETE ON omnix_trading_strategy_configs
FOR EACH ROW EXECUTE FUNCTION omnix_trading_archive_strategy_before_delete();
