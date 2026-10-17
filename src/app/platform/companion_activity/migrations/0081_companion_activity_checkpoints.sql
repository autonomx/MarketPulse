CREATE TABLE IF NOT EXISTS omnix_companion_activity_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    activity_id TEXT NOT NULL,
    character_id TEXT,
    revision BIGINT NOT NULL CHECK (revision >= 0),
    generation TEXT,
    reason TEXT NOT NULL,
    sensitivity TEXT NOT NULL CHECK (sensitivity IN ('sensitive', 'secret')),
    source_proposition_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    state_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    schema_version TEXT NOT NULL DEFAULT 'companion-activity-checkpoint@1'
);

CREATE UNIQUE INDEX IF NOT EXISTS omnix_companion_activity_checkpoint_revision_uq
    ON omnix_companion_activity_checkpoints(activity_id, revision, reason);

CREATE INDEX IF NOT EXISTS omnix_companion_activity_checkpoint_session_idx
    ON omnix_companion_activity_checkpoints(session_id, created_at DESC);

CREATE INDEX IF NOT EXISTS omnix_companion_activity_checkpoint_activity_idx
    ON omnix_companion_activity_checkpoints(activity_id, revision DESC);
