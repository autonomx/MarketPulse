CREATE TABLE IF NOT EXISTS omnix_users (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    email TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_omnix_users_email
    ON omnix_users (lower(email))
    WHERE email IS NOT NULL;

CREATE TABLE IF NOT EXISTS omnix_workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_by TEXT NOT NULL REFERENCES omnix_users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS omnix_workspace_memberships (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES omnix_users(id) ON DELETE CASCADE,
    roles TEXT[] NOT NULL DEFAULT ARRAY['member']::text[],
    status TEXT NOT NULL DEFAULT 'active',
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_omnix_memberships_user
    ON omnix_workspace_memberships (user_id, status, workspace_id);

CREATE TABLE IF NOT EXISTS omnix_audit_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id TEXT REFERENCES omnix_workspaces(id) ON DELETE SET NULL,
    actor_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    action TEXT NOT NULL,
    trace_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_omnix_audit_workspace_created
    ON omnix_audit_events (workspace_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS omnix_idempotency_keys (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    operation_scope TEXT NOT NULL,
    operation_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'reserved',
    response JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, operation_scope, operation_key)
);

CREATE INDEX IF NOT EXISTS idx_omnix_idempotency_created
    ON omnix_idempotency_keys (created_at, status);
