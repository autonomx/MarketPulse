-- omnix-migration: phase=expand transactional=true
-- TVP-11.3: Omnix Scripts' version history.
--
-- A script is a trading document (record type 'script', payload {"name", "source"}). Each save that changes its
-- source or name adds a version here, in the same transaction as the document (TradingDocumentRepository), keyed by
-- the document revision it was saved as. The editor lists versions, diffs two and restores one by saving it again
-- (a new version). The oldest are dropped beyond SCRIPT_VERSIONS_KEPT per script.

CREATE TABLE IF NOT EXISTS omnix_trading_script_versions (
    workspace_id TEXT NOT NULL,
    script_id TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    name TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    saved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, script_id, revision)
);

ALTER TABLE omnix_trading_script_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_trading_script_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_trading_script_versions;
CREATE POLICY tenant_isolation ON omnix_trading_script_versions
    USING (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on')
    WITH CHECK (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on');
