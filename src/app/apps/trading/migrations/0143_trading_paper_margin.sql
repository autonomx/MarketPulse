-- omnix-migration: phase=expand transactional=true
-- TVP-7.2b: leverage, margin calls and a fixed commission in paper accounts.
--
-- * margin_settings: per asset class ({"equity": {"long_pct": 50, "short_pct": 100}}); a class not
--   listed holds 100% of a position's value, as every account did before, so existing and
--   strategy-owned accounts are unchanged.
-- * commission_type: 'percent' (commission_bps of the notional, as before) or 'fixed_per_order'
--   (commission_fixed once per order, on its first fill).

ALTER TABLE omnix_trading_paper_accounts
    ADD COLUMN IF NOT EXISTS margin_settings JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE omnix_trading_paper_accounts
    ADD COLUMN IF NOT EXISTS commission_type TEXT NOT NULL DEFAULT 'percent';
ALTER TABLE omnix_trading_paper_accounts
    ADD COLUMN IF NOT EXISTS commission_fixed NUMERIC NOT NULL DEFAULT 0;

ALTER TABLE omnix_trading_paper_accounts
    DROP CONSTRAINT IF EXISTS omnix_trading_paper_accounts_commission_type_check;
ALTER TABLE omnix_trading_paper_accounts
    ADD CONSTRAINT omnix_trading_paper_accounts_commission_type_check CHECK (
        commission_type IN ('percent', 'fixed_per_order')
    );
ALTER TABLE omnix_trading_paper_accounts
    DROP CONSTRAINT IF EXISTS omnix_trading_paper_accounts_commission_fixed_check;
ALTER TABLE omnix_trading_paper_accounts
    ADD CONSTRAINT omnix_trading_paper_accounts_commission_fixed_check CHECK (commission_fixed >= 0);
