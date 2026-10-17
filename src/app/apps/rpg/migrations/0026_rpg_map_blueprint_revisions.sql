CREATE TABLE IF NOT EXISTS omnix_rpg_map_blueprint_revisions (
    workspace_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    map_id TEXT NOT NULL,
    blueprint_revision BIGINT NOT NULL CHECK (blueprint_revision >= 1),
    document_jsonb JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    semantic_interface_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ready', 'invalid')),
    findings_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, world_id, map_id, blueprint_revision),
    UNIQUE (workspace_id, world_id, map_id, content_hash),
    FOREIGN KEY (workspace_id, world_id)
        REFERENCES omnix_rpg_worlds (workspace_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_map_blueprint_latest_idx
    ON omnix_rpg_map_blueprint_revisions (
        workspace_id, world_id, map_id, blueprint_revision DESC
    );

CREATE INDEX IF NOT EXISTS omnix_rpg_map_blueprint_status_idx
    ON omnix_rpg_map_blueprint_revisions (
        workspace_id, world_id, status, map_id, blueprint_revision DESC
    );

COMMENT ON TABLE omnix_rpg_map_blueprint_revisions IS
    'Immutable authoring revisions for semantic map requirements before compilation.';

COMMENT ON COLUMN omnix_rpg_map_blueprint_revisions.findings_jsonb IS
    'Scenario-reference reconciliation findings against this blueprint semantic interface.';
