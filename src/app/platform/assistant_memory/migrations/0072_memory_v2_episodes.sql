CREATE TABLE IF NOT EXISTS omnix_memory_v2_episodes (
    episode_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    visibility_scopes JSONB NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    participant_entity_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    importance DOUBLE PRECISION NOT NULL CHECK (importance >= 0.0 AND importance <= 1.0),
    derivation_version TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (episode_id, principal_id, owner_type, owner_id),
    CHECK (ended_at IS NULL OR ended_at >= started_at),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_episodes_space_time
    ON omnix_memory_v2_episodes(principal_id, owner_type, owner_id, started_at DESC);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_episode_observations (
    episode_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    PRIMARY KEY (episode_id, observation_id),
    FOREIGN KEY (episode_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_episodes(episode_id, principal_id, owner_type, owner_id)
        ON DELETE CASCADE,
    FOREIGN KEY (observation_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_observations(observation_id, principal_id, owner_type, owner_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_episode_assertions (
    episode_id TEXT NOT NULL,
    assertion_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    PRIMARY KEY (episode_id, assertion_id),
    FOREIGN KEY (episode_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_episodes(episode_id, principal_id, owner_type, owner_id)
        ON DELETE CASCADE,
    FOREIGN KEY (assertion_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_graph_assertions(assertion_id, principal_id, owner_type, owner_id)
        ON DELETE RESTRICT
);
