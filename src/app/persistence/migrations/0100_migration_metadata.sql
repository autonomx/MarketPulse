-- omnix-migration: phase=contract transactional=true
ALTER TABLE omnix_schema_migrations
    ADD COLUMN IF NOT EXISTS phase TEXT NOT NULL DEFAULT 'contract',
    ADD COLUMN IF NOT EXISTS transactional BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE omnix_schema_migrations
    DROP CONSTRAINT IF EXISTS omnix_schema_migrations_phase_check;

ALTER TABLE omnix_schema_migrations
    ADD CONSTRAINT omnix_schema_migrations_phase_check
    CHECK (phase IN ('expand', 'contract', 'data'));
