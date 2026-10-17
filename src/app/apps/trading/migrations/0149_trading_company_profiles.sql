-- omnix-migration: phase=expand transactional=true
-- TVP-9.3 (D-3, D-5): US company profiles from SEC EDGAR, shared market data (no workspace_id): the SEC CIK, name,
-- SIC code mapped to a sector and industry (heatmaps, screener), and the latest shares outstanding (dei
-- EntityCommonStockSharesOutstanding from the XBRL frames API) for market capitalisation.

CREATE TABLE IF NOT EXISTS omnix_trading_company_profiles (
    ticker TEXT PRIMARY KEY,
    cik TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    sic TEXT,
    sic_description TEXT,
    sector TEXT,
    industry TEXT,
    shares_outstanding NUMERIC,
    shares_as_of DATE,
    profile_fetched_at TIMESTAMPTZ,
    shares_fetched_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS omnix_trading_company_profiles_cik ON omnix_trading_company_profiles (cik);
