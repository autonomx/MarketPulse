CREATE TABLE IF NOT EXISTS omnix_assets (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    module TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    checksum_sha256 TEXT NOT NULL CHECK (length(checksum_sha256) = 64),
    storage_provider TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'active',
    generation_job_id TEXT,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    compat JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, storage_provider, storage_key)
);

CREATE INDEX IF NOT EXISTS idx_omnix_assets_workspace_type
    ON omnix_assets (workspace_id, asset_type, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_omnix_assets_checksum
    ON omnix_assets (workspace_id, checksum_sha256);

CREATE TABLE IF NOT EXISTS omnix_asset_versions (
    asset_id TEXT NOT NULL REFERENCES omnix_assets(id) ON DELETE CASCADE,
    version BIGINT NOT NULL CHECK (version >= 1),
    checksum_sha256 TEXT NOT NULL CHECK (length(checksum_sha256) = 64),
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    storage_provider TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (asset_id, version)
);

CREATE TABLE IF NOT EXISTS omnix_settings (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    setting_scope TEXT NOT NULL,
    setting_key TEXT NOT NULL,
    value JSONB NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_by TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, setting_scope, setting_key)
);

CREATE TABLE IF NOT EXISTS omnix_secret_references (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    secret_reference TEXT NOT NULL,
    provider TEXT NOT NULL,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (workspace_id, secret_reference)
);
