ALTER TABLE omnix_memory_snapshots
    ADD COLUMN IF NOT EXISTS session_id TEXT,
    ADD COLUMN IF NOT EXISTS token_estimate INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS refreshed_at TIMESTAMPTZ;

ALTER TABLE omnix_memory_snapshot_items
    ADD COLUMN IF NOT EXISTS frozen_content TEXT,
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_omnix_memory_snapshots_session
    ON omnix_memory_snapshots (workspace_id, session_id, created_at DESC, id)
    WHERE session_id IS NOT NULL;
