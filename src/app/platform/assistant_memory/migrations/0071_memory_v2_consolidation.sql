CREATE TABLE IF NOT EXISTS omnix_memory_v2_consolidation_state (
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    consolidation_watermark BIGINT NOT NULL DEFAULT 0 CHECK (consolidation_watermark >= 0),
    last_receipt_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (principal_id, owner_type, owner_id),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_consolidation_receipts (
    receipt_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    input_observation_from BIGINT NOT NULL CHECK (input_observation_from >= 1),
    input_observation_through BIGINT NOT NULL CHECK (input_observation_through >= input_observation_from),
    consolidator_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    provider_id TEXT,
    model_id TEXT,
    created_assertion_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    reinforced_assertion_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    superseded_assertion_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    retracted_assertion_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    conflicted_assertion_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_episode_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    relationship_update_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    affect_update_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    resulting_graph_revision BIGINT NOT NULL CHECK (resulting_graph_revision >= 1),
    idempotency_key TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (principal_id, owner_type, owner_id, idempotency_key),
    UNIQUE (receipt_id, principal_id, owner_type, owner_id),
    CHECK (completed_at >= started_at),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_consolidation_state(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_consolidation_receipts_space_range
    ON omnix_memory_v2_consolidation_receipts(
        principal_id, owner_type, owner_id, input_observation_through DESC
    );
