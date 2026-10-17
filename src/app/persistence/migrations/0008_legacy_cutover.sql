CREATE TABLE IF NOT EXISTS omnix_legacy_import_runs (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    source_hash TEXT NOT NULL CHECK (length(source_hash) = 64),
    format_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    discovered_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    imported_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    verification JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_summary JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS omnix_legacy_import_items (
    import_run_id TEXT NOT NULL REFERENCES omnix_legacy_import_runs(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_hash TEXT NOT NULL CHECK (length(source_hash) = 64),
    target_table TEXT,
    target_id TEXT,
    status TEXT NOT NULL,
    error TEXT,
    imported_at TIMESTAMPTZ,
    PRIMARY KEY (import_run_id, entity_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_omnix_legacy_items_status
    ON omnix_legacy_import_items (import_run_id, status, entity_type);

CREATE TABLE IF NOT EXISTS omnix_persistence_cutover (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    mode TEXT NOT NULL DEFAULT 'legacy_preflight',
    import_run_id TEXT REFERENCES omnix_legacy_import_runs(id) ON DELETE SET NULL,
    source_hash TEXT,
    activated_at TIMESTAMPTZ,
    rollback_recorded_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

INSERT INTO omnix_persistence_cutover (singleton, mode)
VALUES (TRUE, 'legacy_preflight')
ON CONFLICT (singleton) DO NOTHING;
