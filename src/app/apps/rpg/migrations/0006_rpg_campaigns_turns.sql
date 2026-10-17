CREATE TABLE IF NOT EXISTS omnix_rpg_campaigns (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    state_jsonb JSONB NOT NULL,
    state_hash TEXT NOT NULL CHECK (length(state_hash) = 64),
    engine_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    seed TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_omnix_rpg_campaigns_workspace_updated
    ON omnix_rpg_campaigns (workspace_id, status, updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS omnix_rpg_turns (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    campaign_id TEXT NOT NULL REFERENCES omnix_rpg_campaigns(id) ON DELETE CASCADE,
    sequence BIGINT NOT NULL CHECK (sequence >= 1),
    submission_id TEXT NOT NULL,
    expected_revision BIGINT NOT NULL CHECK (expected_revision >= 0),
    resulting_revision BIGINT NOT NULL CHECK (resulting_revision >= 1),
    command_jsonb JSONB NOT NULL,
    canonical_effects_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    state_hash_before TEXT NOT NULL CHECK (length(state_hash_before) = 64),
    state_hash_after TEXT NOT NULL CHECK (length(state_hash_after) = 64),
    engine_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    compact_response JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (campaign_id, sequence),
    UNIQUE (campaign_id, submission_id),
    UNIQUE (campaign_id, interaction_id)
);

CREATE INDEX IF NOT EXISTS idx_omnix_rpg_turns_campaign_sequence
    ON omnix_rpg_turns (workspace_id, campaign_id, sequence DESC);

CREATE TABLE IF NOT EXISTS omnix_rpg_interactions (
    interaction_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    campaign_id TEXT NOT NULL REFERENCES omnix_rpg_campaigns(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL REFERENCES omnix_rpg_turns(id) ON DELETE CASCADE,
    sequence BIGINT NOT NULL,
    state_revision BIGINT NOT NULL,
    event_jsonb JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (campaign_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_omnix_rpg_interactions_campaign_sequence
    ON omnix_rpg_interactions (workspace_id, campaign_id, sequence DESC);

CREATE TABLE IF NOT EXISTS omnix_rpg_snapshots (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    campaign_id TEXT NOT NULL REFERENCES omnix_rpg_campaigns(id) ON DELETE CASCADE,
    revision BIGINT NOT NULL CHECK (revision >= 0),
    snapshot_jsonb JSONB,
    blob_asset_id TEXT REFERENCES omnix_assets(id) ON DELETE SET NULL,
    state_hash TEXT NOT NULL CHECK (length(state_hash) = 64),
    engine_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((snapshot_jsonb IS NOT NULL) <> (blob_asset_id IS NOT NULL)),
    UNIQUE (campaign_id, revision)
);

CREATE TABLE IF NOT EXISTS omnix_rpg_participants (
    campaign_id TEXT NOT NULL REFERENCES omnix_rpg_campaigns(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES omnix_users(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    permissions TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
    joined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    left_at TIMESTAMPTZ,
    PRIMARY KEY (campaign_id, user_id)
);
