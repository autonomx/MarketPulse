CREATE TABLE IF NOT EXISTS omnix_rpg_world_generation_runs (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    draft_revision BIGINT NOT NULL CHECK (draft_revision >= 1),
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'running', 'review', 'ready', 'failed', 'canceled')),
    graph_jsonb JSONB NOT NULL,
    context_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    settings_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    plan_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    progress_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, run_id),
    UNIQUE (workspace_id, world_id, draft_revision),
    FOREIGN KEY (workspace_id, world_id)
        REFERENCES omnix_rpg_worlds (workspace_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_world_generation_runs_status_idx
    ON omnix_rpg_world_generation_runs (
        workspace_id, status, updated_at DESC
    );

COMMENT ON TABLE omnix_rpg_world_generation_runs IS
    'Durable DAG coordination state for reusable-world topic generation; topic execution uses generic jobs.';
