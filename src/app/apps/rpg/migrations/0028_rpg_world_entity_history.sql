CREATE TABLE IF NOT EXISTS omnix_rpg_world_entity_history (
    history_sequence BIGSERIAL PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    operation TEXT NOT NULL
        CHECK (operation IN ('manual_edit', 'regenerate', 'restore')),
    before_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    after_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    topic_content_hash TEXT NOT NULL,
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (workspace_id, world_id)
        REFERENCES omnix_rpg_worlds (workspace_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_world_entity_history_latest_idx
    ON omnix_rpg_world_entity_history (
        workspace_id, world_id, topic_id, entity_id, history_sequence DESC
    );

COMMENT ON TABLE omnix_rpg_world_entity_history IS
    'Append-only entity-level authoring history for reusable RPG world topics.';
