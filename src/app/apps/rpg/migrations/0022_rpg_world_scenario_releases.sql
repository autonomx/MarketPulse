CREATE TABLE IF NOT EXISTS omnix_rpg_worlds (
    workspace_id TEXT NOT NULL,
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published', 'archived')),
    source_mode TEXT NOT NULL DEFAULT 'manual'
        CHECK (source_mode IN ('manual', 'ai', 'hybrid', 'imported')),
    genre TEXT NOT NULL DEFAULT 'classic_fantasy',
    tone TEXT NOT NULL DEFAULT 'heroic adventure',
    seed BIGINT NOT NULL DEFAULT 0,
    draft_revision BIGINT NOT NULL DEFAULT 1 CHECK (draft_revision >= 1),
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, id),
    FOREIGN KEY (workspace_id)
        REFERENCES omnix_workspaces (id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_rpg_world_topics (
    workspace_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    draft_revision BIGINT NOT NULL CHECK (draft_revision >= 1),
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'ai', 'imported')),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'ready', 'stale', 'failed')),
    content_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    directives_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    dependency_hashes_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_hash TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    provenance_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, world_id, topic_id),
    FOREIGN KEY (workspace_id, world_id)
        REFERENCES omnix_rpg_worlds (workspace_id, id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_rpg_world_revisions (
    workspace_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    document_jsonb JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, world_id, revision),
    UNIQUE (workspace_id, world_id, content_hash),
    FOREIGN KEY (workspace_id, world_id)
        REFERENCES omnix_rpg_worlds (workspace_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS omnix_rpg_world_releases (
    workspace_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    world_revision BIGINT NOT NULL CHECK (world_revision >= 1),
    release BIGINT NOT NULL CHECK (release >= 1),
    document_jsonb JSONB NOT NULL,
    release_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, world_id, world_revision, release),
    UNIQUE (workspace_id, world_id, release_hash),
    FOREIGN KEY (workspace_id, world_id, world_revision)
        REFERENCES omnix_rpg_world_revisions (workspace_id, world_id, revision)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS omnix_rpg_scenarios (
    workspace_id TEXT NOT NULL,
    id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published', 'archived')),
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, id),
    FOREIGN KEY (workspace_id, world_id)
        REFERENCES omnix_rpg_worlds (workspace_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS omnix_rpg_scenario_revisions (
    workspace_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    world_id TEXT NOT NULL,
    world_revision BIGINT NOT NULL CHECK (world_revision >= 1),
    document_jsonb JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, scenario_id, revision),
    UNIQUE (workspace_id, scenario_id, content_hash),
    FOREIGN KEY (workspace_id, scenario_id)
        REFERENCES omnix_rpg_scenarios (workspace_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, world_id, world_revision)
        REFERENCES omnix_rpg_world_revisions (workspace_id, world_id, revision)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS omnix_rpg_campaign_world_bindings (
    workspace_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    world_revision BIGINT NOT NULL CHECK (world_revision >= 1),
    world_release BIGINT NOT NULL CHECK (world_release >= 1),
    scenario_id TEXT NOT NULL,
    scenario_revision BIGINT NOT NULL CHECK (scenario_revision >= 1),
    binding_jsonb JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, campaign_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, world_id, world_revision, world_release)
        REFERENCES omnix_rpg_world_releases (
            workspace_id, world_id, world_revision, release
        )
        ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, scenario_id, scenario_revision)
        REFERENCES omnix_rpg_scenario_revisions (
            workspace_id, scenario_id, revision
        )
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS omnix_rpg_worlds_status_updated_idx
    ON omnix_rpg_worlds (workspace_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS omnix_rpg_world_topics_status_idx
    ON omnix_rpg_world_topics (workspace_id, world_id, status, topic_id);
CREATE INDEX IF NOT EXISTS omnix_rpg_world_releases_latest_idx
    ON omnix_rpg_world_releases (
        workspace_id, world_id, world_revision, release DESC
    );
CREATE INDEX IF NOT EXISTS omnix_rpg_scenarios_world_idx
    ON omnix_rpg_scenarios (workspace_id, world_id, status, updated_at DESC);

COMMENT ON TABLE omnix_rpg_worlds IS
    'Mutable RPG world authoring projects; immutable canon lives in revisions.';
COMMENT ON TABLE omnix_rpg_world_revisions IS
    'Immutable published RPG world canon and semantic map requirements.';
COMMENT ON TABLE omnix_rpg_world_releases IS
    'Certified compiled artifact sets for an exact RPG world revision.';
COMMENT ON TABLE omnix_rpg_scenario_revisions IS
    'Immutable campaign start profiles pinned to exact world revisions.';
COMMENT ON TABLE omnix_rpg_campaign_world_bindings IS
    'Exact world, release, scenario, and map pins for one RPG campaign.';
