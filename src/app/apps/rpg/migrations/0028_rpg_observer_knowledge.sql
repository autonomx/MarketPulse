CREATE TABLE IF NOT EXISTS omnix_rpg_map_observer_knowledge (
    workspace_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    map_instance_id TEXT NOT NULL,
    observer_actor_id TEXT NOT NULL,
    knowledge_revision BIGINT NOT NULL DEFAULT 0 CHECK (knowledge_revision >= 0),
    observation_sequence BIGINT NOT NULL DEFAULT 0 CHECK (observation_sequence >= 0),
    observed_map_state_revision BIGINT NOT NULL DEFAULT 0
        CHECK (observed_map_state_revision >= 0),
    policy_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    visible_cells_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    known_cells_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    detected_actor_ids_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    known_portal_ids_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    known_spawn_point_ids_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    known_zone_ids_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, campaign_id, map_instance_id, observer_actor_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, map_instance_id)
        REFERENCES omnix_rpg_campaign_map_instances (workspace_id, map_instance_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_rpg_map_observation_events (
    workspace_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    map_instance_id TEXT NOT NULL,
    observer_actor_id TEXT NOT NULL,
    observation_sequence BIGINT NOT NULL CHECK (observation_sequence >= 1),
    event_id TEXT NOT NULL,
    map_state_revision BIGINT NOT NULL CHECK (map_state_revision >= 0),
    event_jsonb JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (
        workspace_id, campaign_id, map_instance_id,
        observer_actor_id, observation_sequence
    ),
    UNIQUE (workspace_id, event_id),
    FOREIGN KEY (
        workspace_id, campaign_id, map_instance_id, observer_actor_id
    ) REFERENCES omnix_rpg_map_observer_knowledge (
        workspace_id, campaign_id, map_instance_id, observer_actor_id
    ) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_map_observer_knowledge_recent_idx
    ON omnix_rpg_map_observer_knowledge (
        workspace_id, campaign_id, observer_actor_id, updated_at DESC
    );
CREATE INDEX IF NOT EXISTS omnix_rpg_map_observation_events_recent_idx
    ON omnix_rpg_map_observation_events (
        workspace_id, campaign_id, map_instance_id,
        observer_actor_id, observation_sequence DESC
    );

COMMENT ON TABLE omnix_rpg_map_observer_knowledge IS
    'Durable observer-owned map knowledge; never authoritative map state.';
COMMENT ON TABLE omnix_rpg_map_observation_events IS
    'Deterministic observation history derived from authoritative map snapshots.';
