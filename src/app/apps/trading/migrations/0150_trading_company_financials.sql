-- omnix-migration: phase=expand transactional=true
-- TVP-10.2 (D-5): a US company's financial statements, normalised from its SEC XBRL company facts (income statement,
-- balance sheet, cash flow; annual and quarterly), cached per company for a day. Shared market data (no workspace_id).

CREATE TABLE IF NOT EXISTS omnix_trading_company_financials (
    cik TEXT PRIMARY KEY,
    statements JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
