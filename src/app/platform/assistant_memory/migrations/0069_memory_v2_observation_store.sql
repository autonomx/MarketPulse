CREATE TABLE IF NOT EXISTS omnix_memory_v2_authority_streams (
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    last_sequence BIGINT NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    observation_watermark BIGINT NOT NULL DEFAULT 0 CHECK (observation_watermark >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (principal_id, owner_type, owner_id),
    CHECK (observation_watermark <= last_sequence)
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_observations (
    observation_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    authority_sequence BIGINT NOT NULL CHECK (authority_sequence >= 1),
    idempotency_key TEXT NOT NULL,
    visibility_kind TEXT NOT NULL CHECK (visibility_kind IN ('global', 'workspace', 'project', 'session')),
    visibility_scope_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'user_said', 'assistant_generated', 'assistant_delivered',
        'assistant_experienced', 'external_observed', 'system_event',
        'imported_legacy_memory', 'acoustic_observation'
    )),
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance JSONB NOT NULL,
    correlation_id TEXT,
    schema_version TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (principal_id, owner_type, owner_id, authority_sequence),
    UNIQUE (principal_id, owner_type, owner_id, idempotency_key),
    UNIQUE (observation_id, principal_id, owner_type, owner_id),
    UNIQUE (observation_id, principal_id, owner_type, owner_id, authority_sequence),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_observations_space_sequence
    ON omnix_memory_v2_observations(principal_id, owner_type, owner_id, authority_sequence);
CREATE INDEX IF NOT EXISTS idx_memory_v2_observations_visibility
    ON omnix_memory_v2_observations(principal_id, owner_type, owner_id, visibility_kind, visibility_scope_id);
CREATE INDEX IF NOT EXISTS idx_memory_v2_observations_correlation
    ON omnix_memory_v2_observations(correlation_id)
    WHERE correlation_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS omnix_memory_v2_observation_dispositions (
    observation_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    authority_sequence BIGINT NOT NULL CHECK (authority_sequence >= 1),
    state TEXT NOT NULL CHECK (state IN ('active', 'revoked', 'purged')),
    changed_at TIMESTAMPTZ NOT NULL,
    reason TEXT,
    actor_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (observation_id, principal_id, owner_type, owner_id, authority_sequence)
        REFERENCES omnix_memory_v2_observations(
            observation_id, principal_id, owner_type, owner_id, authority_sequence
        ) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_dispositions_space
    ON omnix_memory_v2_observation_dispositions(principal_id, owner_type, owner_id, state);
