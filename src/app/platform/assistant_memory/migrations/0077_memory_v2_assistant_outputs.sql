CREATE TABLE IF NOT EXISTS omnix_memory_v2_assistant_outputs (
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    visibility_kind TEXT NOT NULL CHECK (visibility_kind IN ('global', 'workspace', 'project', 'session')),
    visibility_scope_id TEXT NOT NULL,
    generated_text TEXT NOT NULL,
    delivered_text TEXT,
    experienced_prefix TEXT NOT NULL DEFAULT '',
    generated_observation_id TEXT NOT NULL,
    delivered_observation_id TEXT,
    experienced_observation_id TEXT,
    finalized BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (principal_id, owner_type, owner_id, correlation_id),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE,
    FOREIGN KEY (generated_observation_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_observations(observation_id, principal_id, owner_type, owner_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (delivered_observation_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_observations(observation_id, principal_id, owner_type, owner_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (experienced_observation_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_observations(observation_id, principal_id, owner_type, owner_id)
        ON DELETE RESTRICT,
    CHECK (delivered_text IS NOT NULL OR delivered_observation_id IS NULL),
    CHECK (experienced_observation_id IS NULL OR finalized)
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_assistant_outputs_correlation
    ON omnix_memory_v2_assistant_outputs(correlation_id);
