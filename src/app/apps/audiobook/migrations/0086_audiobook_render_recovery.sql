-- A damaged or missing immutable blob may require a new audio asset for the
-- same deterministic request. Keep the old render as evidence, never reuse its ID.
ALTER TABLE omnix_audiobook_renders
    DROP CONSTRAINT IF EXISTS omnix_audiobook_renders_workspace_id_render_key_key;

ALTER TABLE omnix_audiobook_renders
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'completed';

CREATE INDEX IF NOT EXISTS idx_audiobook_renders_cache
    ON omnix_audiobook_renders (workspace_id, render_key, created_at DESC, id DESC)
    WHERE status = 'completed';
