CREATE TABLE IF NOT EXISTS omnix_memory_v2_shadow_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    observation_watermark BIGINT NOT NULL CHECK (observation_watermark >= 0),
    graph_revision BIGINT NOT NULL CHECK (graph_revision >= 0),
    v1_result_count INTEGER NOT NULL CHECK (v1_result_count >= 0),
    v2_result_count INTEGER NOT NULL CHECK (v2_result_count >= 0),
    matched_v1_count INTEGER NOT NULL CHECK (matched_v1_count >= 0),
    recall DOUBLE PRECISION NOT NULL CHECK (recall >= 0.0 AND recall <= 1.0),
    precision DOUBLE PRECISION NOT NULL CHECK (precision >= 0.0 AND precision <= 1.0),
    mean_best_similarity DOUBLE PRECISION NOT NULL CHECK (mean_best_similarity >= 0.0 AND mean_best_similarity <= 1.0),
    similarity_threshold DOUBLE PRECISION NOT NULL CHECK (similarity_threshold >= 0.0 AND similarity_threshold <= 1.0),
    recall_threshold DOUBLE PRECISION NOT NULL CHECK (recall_threshold >= 0.0 AND recall_threshold <= 1.0),
    precision_threshold DOUBLE PRECISION NOT NULL CHECK (precision_threshold >= 0.0 AND precision_threshold <= 1.0),
    passed BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_shadow_evaluations_space_time
    ON omnix_memory_v2_shadow_evaluations(
        principal_id, owner_type, owner_id, created_at DESC
    );
