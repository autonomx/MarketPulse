-- Audiobook projects retain immutable source and production history while
-- allowing users to remove the project from their active library.
ALTER TABLE omnix_audiobook_projects
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_audiobook_projects_active
    ON omnix_audiobook_projects(workspace_id, updated_at DESC)
    WHERE deleted_at IS NULL;
