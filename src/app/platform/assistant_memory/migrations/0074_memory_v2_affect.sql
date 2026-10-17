CREATE TABLE IF NOT EXISTS omnix_memory_v2_affect_observations (
    affect_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    source_observation_id TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('explicit', 'semantic', 'acoustic', 'fused')),
    observed_at TIMESTAMPTZ NOT NULL,
    valence DOUBLE PRECISION CHECK (valence IS NULL OR (valence >= -1.0 AND valence <= 1.0)),
    arousal DOUBLE PRECISION CHECK (arousal IS NULL OR (arousal >= -1.0 AND arousal <= 1.0)),
    emotion_distribution JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    model_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (affect_id, principal_id, owner_type, owner_id),
    CHECK (valence IS NOT NULL OR arousal IS NOT NULL OR emotion_distribution <> '{}'::jsonb),
    FOREIGN KEY (source_observation_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_observations(observation_id, principal_id, owner_type, owner_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_affect_space_time
    ON omnix_memory_v2_affect_observations(
        principal_id, owner_type, owner_id, observed_at DESC
    );
CREATE INDEX IF NOT EXISTS idx_memory_v2_affect_source_observation
    ON omnix_memory_v2_affect_observations(source_observation_id);
