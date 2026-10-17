-- omnix-migration: phase=contract transactional=true
CREATE TABLE IF NOT EXISTS omnix_settings_entries (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value JSONB NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1,
    updated_by TEXT NULL REFERENCES omnix_users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, key),
    CONSTRAINT omnix_settings_entries_revision_positive CHECK (revision >= 1)
);

INSERT INTO omnix_settings_entries (workspace_id, key, value, revision, updated_by, updated_at)
SELECT
    workspace_id,
    CASE
        WHEN setting_scope IN ('global', 'workspace') THEN setting_key
        ELSE setting_scope || '.' || setting_key
    END,
    value,
    GREATEST(revision, 1),
    updated_by,
    updated_at
FROM omnix_settings
ON CONFLICT (workspace_id, key) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_omnix_settings_entries_updated
    ON omnix_settings_entries (workspace_id, updated_at DESC);
