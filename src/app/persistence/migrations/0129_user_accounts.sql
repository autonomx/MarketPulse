-- omnix-migration: phase=expand transactional=true
-- Accounts in local mode: email and password sign-in, guest accounts, invite
-- links, "stay signed in" sessions, and Google sign-in or linking (WP-4.1).

-- A guest account ends when its session does; 'installation' is user:local.
ALTER TABLE omnix_users
    ADD COLUMN IF NOT EXISTS account_kind TEXT NOT NULL DEFAULT 'standard';
ALTER TABLE omnix_users DROP CONSTRAINT IF EXISTS omnix_users_account_kind_check;
ALTER TABLE omnix_users
    ADD CONSTRAINT omnix_users_account_kind_check CHECK (account_kind IN ('standard', 'guest'));

-- One password per user, scrypt like the install credential.
CREATE TABLE IF NOT EXISTS omnix_user_passwords (
    user_id TEXT PRIMARY KEY REFERENCES omnix_users(id) ON DELETE CASCADE,
    salt BYTEA NOT NULL,
    credential_hash BYTEA NOT NULL,
    scrypt_n INTEGER NOT NULL CHECK (scrypt_n >= 16384),
    scrypt_r INTEGER NOT NULL CHECK (scrypt_r >= 8),
    scrypt_p INTEGER NOT NULL CHECK (scrypt_p >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON TABLE omnix_user_passwords IS 'omnix:tenant-exempt: sign-in credentials precede workspace membership';

-- Single-use invite links that let someone register while registration is
-- invite-only. The new account gets its own workspace.
CREATE TABLE IF NOT EXISTS omnix_auth_invites (
    token_hash TEXT PRIMARY KEY,
    created_by TEXT NOT NULL REFERENCES omnix_users(id) ON DELETE CASCADE,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    consumed_by TEXT REFERENCES omnix_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_omnix_auth_invites_creator ON omnix_auth_invites (created_by, expires_at);
COMMENT ON TABLE omnix_auth_invites IS 'omnix:tenant-exempt: redeemed before the new account has a workspace';

-- "Stay signed in" sessions slide by their own idle lifetime.
ALTER TABLE omnix_auth_sessions
    ADD COLUMN IF NOT EXISTS sliding_seconds INTEGER CHECK (sliding_seconds IS NULL OR sliding_seconds > 0);
ALTER TABLE omnix_auth_sessions DROP CONSTRAINT IF EXISTS omnix_auth_sessions_auth_method_check;
ALTER TABLE omnix_auth_sessions
    ADD CONSTRAINT omnix_auth_sessions_auth_method_check
    CHECK (auth_method IN ('local', 'oidc', 'password', 'google', 'guest'));

-- One OIDC transaction table serves the organization's IdP and Google; a
-- Google transaction may link Google to a signed-in account, or carry an invite.
ALTER TABLE omnix_oidc_login_states
    ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'oidc';
ALTER TABLE omnix_oidc_login_states DROP CONSTRAINT IF EXISTS omnix_oidc_login_states_purpose_check;
ALTER TABLE omnix_oidc_login_states
    ADD CONSTRAINT omnix_oidc_login_states_purpose_check CHECK (purpose IN ('oidc', 'google', 'google_link'));
ALTER TABLE omnix_oidc_login_states
    ADD COLUMN IF NOT EXISTS link_user_id TEXT REFERENCES omnix_users(id) ON DELETE CASCADE;
ALTER TABLE omnix_oidc_login_states
    ADD COLUMN IF NOT EXISTS invite_token_hash TEXT;
ALTER TABLE omnix_oidc_login_states
    ADD COLUMN IF NOT EXISTS remember BOOLEAN NOT NULL DEFAULT false;
