CREATE TABLE IF NOT EXISTS omnix_rpg_campaign_genesis_runs (
    workspace_id TEXT NOT NULL,
    genesis_run_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('planned', 'generating', 'auditing', 'compiling', 'materializing', 'ready', 'failed')),
    depth TEXT NOT NULL CHECK (depth IN ('quick', 'standard', 'epic')),
    topic_graph_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    jobs_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    progress_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    audit_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    bible_revision BIGINT,
    bible_content_hash TEXT NOT NULL DEFAULT '',
    error_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, genesis_run_id),
    UNIQUE (workspace_id, campaign_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_campaign_genesis_status_idx
    ON omnix_rpg_campaign_genesis_runs (workspace_id, status, updated_at DESC);

COMMENT ON TABLE omnix_rpg_campaign_genesis_runs IS
    'Campaign World Forge progress and launch-gate evidence for one RPG campaign.';
