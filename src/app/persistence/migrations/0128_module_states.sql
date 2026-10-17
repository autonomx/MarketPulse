-- omnix-migration: phase=expand transactional=true
-- PA-4.3: each module's lifecycle state, read by every process. 'draining' and
-- 'retired' override the runtime configuration: the module accepts no new work.
-- 'active' (or no row) defers to the configuration.
CREATE TABLE IF NOT EXISTS omnix_module_states (
    module_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('active', 'draining', 'retired')),
    drain_deadline TIMESTAMPTZ,
    reason TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE omnix_module_states IS 'omnix:tenant-exempt: installation-wide module lifecycle state, no workspace data';
