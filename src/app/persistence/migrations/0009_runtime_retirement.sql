CREATE TABLE IF NOT EXISTS omnix_runtime_persistence_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    backend TEXT NOT NULL DEFAULT 'postgresql' CHECK (backend = 'postgresql'),
    runtime_schema_version TEXT NOT NULL DEFAULT 'phase9',
    legacy_runtime_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    activated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

INSERT INTO omnix_runtime_persistence_state (
    singleton, backend, runtime_schema_version, legacy_runtime_enabled, metadata
)
VALUES (
    TRUE,
    'postgresql',
    'phase9',
    FALSE,
    jsonb_build_object('sqlite_runtime_retired', TRUE)
)
ON CONFLICT (singleton) DO UPDATE SET
    backend = 'postgresql',
    runtime_schema_version = 'phase9',
    legacy_runtime_enabled = FALSE,
    updated_at = CURRENT_TIMESTAMP,
    metadata = omnix_runtime_persistence_state.metadata
        || jsonb_build_object('sqlite_runtime_retired', TRUE);
