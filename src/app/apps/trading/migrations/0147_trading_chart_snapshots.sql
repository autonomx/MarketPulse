-- omnix-migration: phase=expand transactional=true
-- TVP-2.1/2.5: chart snapshots behind a link. A snapshot is a PNG of a chart (at most 3 MB); its id is random and
-- unguessable, and the link opens for signed-in members of the workspace. The newest 200 per workspace are kept.

CREATE TABLE IF NOT EXISTS omnix_trading_chart_snapshots (
    workspace_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    instrument_id TEXT NOT NULL DEFAULT '',
    interval TEXT NOT NULL DEFAULT '',
    image BYTEA NOT NULL CHECK (octet_length(image) <= 3145728),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, snapshot_id)
);

CREATE INDEX IF NOT EXISTS omnix_trading_chart_snapshots_recent ON omnix_trading_chart_snapshots (workspace_id, created_at DESC);

ALTER TABLE omnix_trading_chart_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_trading_chart_snapshots FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_trading_chart_snapshots;
CREATE POLICY tenant_isolation ON omnix_trading_chart_snapshots
    USING (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on')
    WITH CHECK (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on');
