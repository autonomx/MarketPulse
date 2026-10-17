-- omnix-migration: phase=expand transactional=true
-- Market reference data is shared by every workspace: public company facts,
-- filings and market-wide statistics fetched from providers, with no workspace,
-- user or account data. Record why these tables are not tenant-isolated.
COMMENT ON TABLE omnix_trading_company_profiles IS 'omnix:tenant-exempt: public company reference data shared by every workspace';
COMMENT ON TABLE omnix_trading_company_financials IS 'omnix:tenant-exempt: public company financial statements shared by every workspace';
COMMENT ON TABLE omnix_trading_fundamental_snapshots IS 'omnix:tenant-exempt: public filing snapshots shared by every workspace';
COMMENT ON TABLE omnix_trading_corporate_events IS 'omnix:tenant-exempt: public corporate events shared by every workspace';
COMMENT ON TABLE omnix_trading_market_breadth IS 'omnix:tenant-exempt: market-wide breadth statistics shared by every workspace';
