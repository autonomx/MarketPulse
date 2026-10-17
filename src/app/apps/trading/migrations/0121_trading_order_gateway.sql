-- omnix-migration: phase=expand transactional=true
-- WP-8.3: the order gateway's database-backed controls.
-- A kill switch stops orders that open or add exposure; orders that only
-- reduce a long position still go through. Scopes: the whole workspace
-- ('global', scope_id ''), one paper account, or one strategy. A missing row
-- means the switch is released.
CREATE TABLE IF NOT EXISTS omnix_trading_kill_switches (
    workspace_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('global', 'account', 'strategy')),
    scope_id TEXT NOT NULL DEFAULT '',
    engaged BOOLEAN NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    updated_by TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, scope, scope_id),
    CHECK ((scope = 'global') = (scope_id = ''))
);

ALTER TABLE omnix_trading_kill_switches ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_trading_kill_switches FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_trading_kill_switches;
CREATE POLICY tenant_isolation ON omnix_trading_kill_switches
    USING (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on')
    WITH CHECK (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on');

-- Paper accounts are long-only: a sell must be covered by an unreserved long
-- position unless the account opts in to shorting.
ALTER TABLE omnix_trading_paper_accounts
    ADD COLUMN IF NOT EXISTS allow_short BOOLEAN NOT NULL DEFAULT FALSE;
