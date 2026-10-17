CREATE TABLE IF NOT EXISTS omnix_backup_generations (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'preparing'
        CHECK (status IN ('preparing', 'manifested', 'database_backed_up', 'verified', 'failed')),
    software_revision TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    blob_root TEXT NOT NULL,
    database_backup_reference TEXT,
    manifest_hash TEXT,
    asset_count BIGINT NOT NULL DEFAULT 0 CHECK (asset_count >= 0),
    total_blob_bytes BIGINT NOT NULL DEFAULT 0 CHECK (total_blob_bytes >= 0),
    rpo_seconds INTEGER NOT NULL DEFAULT 86400 CHECK (rpo_seconds >= 0),
    rto_seconds INTEGER NOT NULL DEFAULT 3600 CHECK (rto_seconds >= 0),
    encryption_required BOOLEAN NOT NULL DEFAULT TRUE,
    retention_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    manifested_at TIMESTAMPTZ,
    verified_at TIMESTAMPTZ,
    failure JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS omnix_backup_blob_manifest (
    generation_id TEXT NOT NULL REFERENCES omnix_backup_generations(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES omnix_assets(id) ON DELETE RESTRICT,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE RESTRICT,
    storage_provider TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL CHECK (length(checksum_sha256) = 64),
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    lifecycle_status TEXT NOT NULL,
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    verification_error TEXT,
    PRIMARY KEY (generation_id, asset_id),
    UNIQUE (generation_id, storage_provider, storage_key)
);

CREATE INDEX IF NOT EXISTS idx_omnix_backup_generations_status_created
    ON omnix_backup_generations (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_omnix_backup_manifest_generation_verified
    ON omnix_backup_blob_manifest (generation_id, verified, asset_id);

ALTER TABLE omnix_assets
    ADD COLUMN IF NOT EXISTS deletion_not_before TIMESTAMPTZ;
