CREATE TABLE IF NOT EXISTS omnix_rpg_map_definitions (
    workspace_id TEXT NOT NULL,
    map_id TEXT NOT NULL,
    definition_revision BIGINT NOT NULL CHECK (definition_revision >= 1),
    world_id TEXT NOT NULL,
    world_revision BIGINT NOT NULL CHECK (world_revision >= 1),
    document_jsonb JSONB NOT NULL,
    definition_hash TEXT NOT NULL,
    semantic_interface_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, map_id, definition_revision),
    UNIQUE (workspace_id, map_id, definition_hash),
    FOREIGN KEY (workspace_id, world_id, world_revision)
        REFERENCES omnix_rpg_world_revisions (workspace_id, world_id, revision)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS omnix_rpg_campaign_map_instances (
    workspace_id TEXT NOT NULL,
    map_instance_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    map_id TEXT NOT NULL,
    map_definition_revision BIGINT NOT NULL CHECK (map_definition_revision >= 1),
    definition_hash TEXT NOT NULL,
    map_state_revision BIGINT NOT NULL DEFAULT 0 CHECK (map_state_revision >= 0),
    applied_event_sequence BIGINT NOT NULL DEFAULT 0 CHECK (applied_event_sequence >= 0),
    snapshot_jsonb JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, map_instance_id),
    UNIQUE (workspace_id, campaign_id, location_id, map_instance_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, map_id, map_definition_revision)
        REFERENCES omnix_rpg_map_definitions (
            workspace_id, map_id, definition_revision
        )
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS omnix_rpg_campaign_map_events (
    workspace_id TEXT NOT NULL,
    map_instance_id TEXT NOT NULL,
    event_sequence BIGINT NOT NULL CHECK (event_sequence >= 1),
    event_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    map_state_revision_before BIGINT NOT NULL CHECK (map_state_revision_before >= 0),
    map_state_revision_after BIGINT NOT NULL CHECK (map_state_revision_after >= 1),
    payload_jsonb JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, map_instance_id, event_sequence),
    UNIQUE (workspace_id, event_id),
    UNIQUE (workspace_id, map_instance_id, command_id),
    FOREIGN KEY (workspace_id, map_instance_id)
        REFERENCES omnix_rpg_campaign_map_instances (workspace_id, map_instance_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_map_definitions_world_idx
    ON omnix_rpg_map_definitions (
        workspace_id, world_id, world_revision, map_id, definition_revision DESC
    );
CREATE INDEX IF NOT EXISTS omnix_rpg_campaign_map_instances_campaign_idx
    ON omnix_rpg_campaign_map_instances (
        workspace_id, campaign_id, updated_at DESC
    );
CREATE INDEX IF NOT EXISTS omnix_rpg_campaign_map_events_lookup_idx
    ON omnix_rpg_campaign_map_events (
        workspace_id, map_instance_id, event_sequence
    );

COMMENT ON TABLE omnix_rpg_map_definitions IS
    'Immutable independently revisioned authoritative RPG grid map definitions.';
COMMENT ON TABLE omnix_rpg_campaign_map_instances IS
    'Campaign-owned bindings and reducer snapshots for exact map definitions.';
COMMENT ON TABLE omnix_rpg_campaign_map_events IS
    'Authoritative resolved map events; replay applies these outcomes directly.';
