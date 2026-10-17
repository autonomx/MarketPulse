ALTER TABLE omnix_persistence_cutover
    ADD COLUMN IF NOT EXISTS authority_state TEXT NOT NULL DEFAULT 'legacy_preflight'
        CHECK (authority_state IN (
            'legacy_preflight',
            'imported_unverified',
            'imported_verified',
            'postgresql_activated_frozen',
            'postgresql_open_for_writes',
            'postgresql_stabilized',
            'rollback_recorded'
        )),
    ADD COLUMN IF NOT EXISTS backup_generation_id TEXT
        REFERENCES omnix_backup_generations(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS opened_for_writes_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS stabilized_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS latest_authoritative_revision TEXT,
    ADD COLUMN IF NOT EXISTS destructive_override_at TIMESTAMPTZ;

UPDATE omnix_persistence_cutover
   SET authority_state = CASE
       WHEN mode = 'postgresql' THEN 'postgresql_stabilized'
       WHEN mode = 'rollback_recorded' THEN 'rollback_recorded'
       ELSE 'legacy_preflight'
   END
 WHERE authority_state = 'legacy_preflight';

CREATE TABLE IF NOT EXISTS omnix_cutover_transitions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    import_run_id TEXT REFERENCES omnix_legacy_import_runs(id) ON DELETE SET NULL,
    backup_generation_id TEXT REFERENCES omnix_backup_generations(id) ON DELETE SET NULL,
    software_revision TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    operator_note TEXT,
    destructive_acknowledgement BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_omnix_cutover_transitions_created
    ON omnix_cutover_transitions (created_at DESC, id DESC);
