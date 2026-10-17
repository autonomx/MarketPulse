CREATE TABLE IF NOT EXISTS omnix_provider_configs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    provider_type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    secret_reference TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, secret_reference)
        REFERENCES omnix_secret_references(workspace_id, secret_reference)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_omnix_provider_configs_workspace
    ON omnix_provider_configs (workspace_id, enabled, provider_type, id);

CREATE TABLE IF NOT EXISTS omnix_provider_status_projections (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL REFERENCES omnix_provider_configs(id) ON DELETE CASCADE,
    status JSONB NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, provider_id)
);

CREATE TABLE IF NOT EXISTS omnix_prompt_templates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    template_type TEXT NOT NULL,
    content TEXT NOT NULL,
    variables JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'active',
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_omnix_prompt_templates_workspace
    ON omnix_prompt_templates (workspace_id, status, updated_at DESC, id);

CREATE TABLE IF NOT EXISTS omnix_research_records (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    research_type TEXT NOT NULL,
    query_text TEXT,
    result_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_fingerprint TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    expires_at TIMESTAMPTZ,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_omnix_research_workspace_status
    ON omnix_research_records (workspace_id, status, updated_at DESC, id);

CREATE TABLE IF NOT EXISTS omnix_reports (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    report_type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    blob_asset_id TEXT REFERENCES omnix_assets(id) ON DELETE SET NULL,
    generated_by_job_id TEXT REFERENCES omnix_jobs(id) ON DELETE SET NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_omnix_reports_workspace_type
    ON omnix_reports (workspace_id, report_type, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS omnix_module_records (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    module TEXT NOT NULL,
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, module, record_type, record_id)
);

CREATE INDEX IF NOT EXISTS idx_omnix_module_records_lookup
    ON omnix_module_records (workspace_id, module, record_type, status, updated_at DESC, record_id);

CREATE TABLE IF NOT EXISTS omnix_runtime_projections (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    projection_type TEXT NOT NULL,
    projection_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    source_revision BIGINT,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, projection_type, projection_key)
);

CREATE INDEX IF NOT EXISTS idx_omnix_runtime_projection_expiry
    ON omnix_runtime_projections (expires_at)
    WHERE expires_at IS NOT NULL;
