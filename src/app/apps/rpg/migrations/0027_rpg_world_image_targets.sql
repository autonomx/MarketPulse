CREATE TABLE IF NOT EXISTS omnix_rpg_world_image_targets (
    workspace_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    entity_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    source_content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'missing'
        CHECK (status IN ('missing', 'queued', 'generating', 'ready', 'failed', 'stale', 'rejected')),
    review_state TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_state IN ('pending', 'approved', 'rejected')),
    suggested_prompt TEXT NOT NULL DEFAULT '',
    active_asset_id TEXT,
    latest_job_id TEXT,
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, world_id, target_id),
    FOREIGN KEY (workspace_id, world_id)
        REFERENCES omnix_rpg_worlds(workspace_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_world_image_targets_world_idx
    ON omnix_rpg_world_image_targets (
        workspace_id, world_id, status, updated_at DESC
    );

CREATE TABLE IF NOT EXISTS omnix_rpg_world_image_attempts (
    workspace_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    source_content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    asset_id TEXT,
    error_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, job_id),
    FOREIGN KEY (workspace_id, world_id, target_id)
        REFERENCES omnix_rpg_world_image_targets(workspace_id, world_id, target_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_world_image_attempts_target_idx
    ON omnix_rpg_world_image_attempts (
        workspace_id, world_id, target_id, created_at DESC
    );
