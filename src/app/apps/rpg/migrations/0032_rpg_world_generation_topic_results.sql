CREATE TABLE IF NOT EXISTS omnix_rpg_world_generation_topic_results (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    draft_revision BIGINT NOT NULL CHECK (draft_revision >= 1),
    topic_id TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('accepted', 'needs_review', 'failed', 'blocked')),
    candidate_jsonb JSONB,
    candidate_hash TEXT NOT NULL DEFAULT '',
    validation_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    provider_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    dependency_hashes_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    dependency_trust_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    job_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, topic_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_rpg_world_generation_runs (workspace_id, run_id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, world_id)
        REFERENCES omnix_rpg_worlds (workspace_id, id)
        ON DELETE CASCADE,
    CHECK (
        (status IN ('accepted', 'needs_review') AND candidate_jsonb IS NOT NULL AND candidate_hash <> '')
        OR (status IN ('failed', 'blocked') AND candidate_jsonb IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS omnix_rpg_world_generation_topic_results_status_idx
    ON omnix_rpg_world_generation_topic_results (
        workspace_id, run_id, status, topic_id
    );

CREATE INDEX IF NOT EXISTS omnix_rpg_world_generation_topic_results_analysis_idx
    ON omnix_rpg_world_generation_topic_results (
        workspace_id, world_id, draft_revision, status, updated_at DESC
    );

COMMENT ON TABLE omnix_rpg_world_generation_topic_results IS
    'Immutable per-run World Forge candidates and validation outcomes. Only accepted candidates may be promoted to authoring topics.';

COMMENT ON COLUMN omnix_rpg_world_generation_topic_results.dependency_trust_jsonb IS
    'Trust state for each dependency used by this generation unit: accepted or quarantined.';
