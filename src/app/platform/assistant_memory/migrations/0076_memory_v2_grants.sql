CREATE TABLE IF NOT EXISTS omnix_memory_v2_grants (
    grant_id TEXT PRIMARY KEY,
    source_principal_id TEXT NOT NULL,
    source_owner_type TEXT NOT NULL CHECK (source_owner_type IN ('system', 'character')),
    source_owner_id TEXT NOT NULL,
    target_principal_id TEXT NOT NULL,
    target_owner_type TEXT NOT NULL CHECK (target_owner_type IN ('system', 'character')),
    target_owner_id TEXT NOT NULL,
    access TEXT NOT NULL CHECK (access = 'read'),
    allowed_domains JSONB NOT NULL,
    max_sensitivity TEXT NOT NULL CHECK (max_sensitivity IN ('normal', 'sensitive', 'secret')),
    scope_constraints JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CHECK (source_principal_id = target_principal_id),
    CHECK (
        source_principal_id <> target_principal_id
        OR source_owner_type <> target_owner_type
        OR source_owner_id <> target_owner_id
    ),
    CHECK (jsonb_typeof(allowed_domains) = 'array' AND jsonb_array_length(allowed_domains) > 0),
    CHECK (jsonb_typeof(scope_constraints) = 'array'),
    FOREIGN KEY (source_principal_id, source_owner_type, source_owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE,
    FOREIGN KEY (target_principal_id, target_owner_type, target_owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_grants_target_active
    ON omnix_memory_v2_grants(
        target_principal_id, target_owner_type, target_owner_id, created_at, revoked_at
    );

CREATE INDEX IF NOT EXISTS idx_memory_v2_grants_source
    ON omnix_memory_v2_grants(
        source_principal_id, source_owner_type, source_owner_id
    );
