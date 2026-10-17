-- omnix-migration: phase=expand transactional=true
-- MarketPulse S-5: Google sign-in for the desktop app.
--
-- Google refuses embedded browsers, so the desktop app signs in through the system browser. The app's window starts
-- a handoff and keeps its nonce; the system browser opens a one-time begin link, signs in with Google and returns to
-- the usual callback, which records the identity on the handoff instead of signing the browser in. The window then
-- trades its nonce for a session on its own cookies. Rows live ten minutes and are used once.

ALTER TABLE omnix_oidc_login_states DROP CONSTRAINT IF EXISTS omnix_oidc_login_states_purpose_check;
ALTER TABLE omnix_oidc_login_states
    ADD CONSTRAINT omnix_oidc_login_states_purpose_check
    CHECK (purpose IN ('oidc', 'google', 'google_link', 'google_desktop', 'google_link_desktop'));

CREATE TABLE IF NOT EXISTS omnix_auth_desktop_handoffs (
    nonce_hash TEXT PRIMARY KEY,
    begin_token_hash TEXT NOT NULL UNIQUE,
    state_hash TEXT UNIQUE,
    purpose TEXT NOT NULL CHECK (purpose IN ('google', 'google_link')),
    link_user_id TEXT REFERENCES omnix_users(id) ON DELETE CASCADE,
    invite_token_hash TEXT,
    remember BOOLEAN NOT NULL DEFAULT TRUE,
    redirect_after TEXT NOT NULL DEFAULT '/',
    user_id TEXT REFERENCES omnix_users(id) ON DELETE CASCADE,
    workspace_id TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    begun_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    consumed_at TIMESTAMPTZ,
    CONSTRAINT ck_omnix_auth_desktop_handoffs_link CHECK ((purpose = 'google_link') = (link_user_id IS NOT NULL)),
    CONSTRAINT ck_omnix_auth_desktop_handoffs_outcome CHECK (
        completed_at IS NULL OR (user_id IS NOT NULL) <> (error IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_omnix_auth_desktop_handoffs_expiry
    ON omnix_auth_desktop_handoffs (expires_at);
