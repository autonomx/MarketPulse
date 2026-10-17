-- omnix-migration: phase=expand transactional=true
-- WP-4.1: browser sessions, the local install credential, single-use launcher
-- login codes, OIDC login transactions and external identity links.

CREATE TABLE IF NOT EXISTS omnix_auth_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES omnix_users(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    auth_method TEXT NOT NULL CHECK (auth_method IN ('local', 'oidc')),
    csrf_secret TEXT NOT NULL,
    user_agent_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    absolute_expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CHECK (expires_at <= absolute_expires_at)
);

CREATE INDEX IF NOT EXISTS idx_omnix_auth_sessions_user
    ON omnix_auth_sessions (user_id, revoked_at);

CREATE INDEX IF NOT EXISTS idx_omnix_auth_sessions_expiry
    ON omnix_auth_sessions (absolute_expires_at);

CREATE TABLE IF NOT EXISTS omnix_install_credentials (
    id TEXT PRIMARY KEY CHECK (id = 'install'),
    user_id TEXT NOT NULL REFERENCES omnix_users(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    salt BYTEA NOT NULL,
    credential_hash BYTEA NOT NULL,
    scrypt_n INTEGER NOT NULL CHECK (scrypt_n >= 16384),
    scrypt_r INTEGER NOT NULL CHECK (scrypt_r >= 8),
    scrypt_p INTEGER NOT NULL CHECK (scrypt_p >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    rotated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS omnix_auth_login_codes (
    code_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES omnix_users(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_omnix_auth_login_codes_expiry
    ON omnix_auth_login_codes (expires_at);

CREATE TABLE IF NOT EXISTS omnix_oidc_login_states (
    state_hash TEXT PRIMARY KEY,
    browser_binding_hash TEXT NOT NULL,
    nonce TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    redirect_after TEXT NOT NULL DEFAULT '/',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_omnix_oidc_login_states_expiry
    ON omnix_oidc_login_states (expires_at);

CREATE TABLE IF NOT EXISTS omnix_external_identities (
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES omnix_users(id) ON DELETE CASCADE,
    email TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (issuer, subject)
);

CREATE INDEX IF NOT EXISTS idx_omnix_external_identities_user
    ON omnix_external_identities (user_id);
