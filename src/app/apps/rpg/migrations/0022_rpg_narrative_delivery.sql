CREATE TABLE IF NOT EXISTS omnix_rpg_narrative_deliveries (
    workspace_id TEXT NOT NULL,
    response_id TEXT NOT NULL,
    semantic_hash TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('blocking', 'deferred')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'streaming', 'complete', 'cancelled')),
    block_ids_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    delivered_block_ids_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    next_index BIGINT NOT NULL DEFAULT 0 CHECK (next_index >= 0),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    cancel_reason TEXT NOT NULL DEFAULT '',
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, response_id),
    FOREIGN KEY (workspace_id, response_id)
        REFERENCES omnix_rpg_narrative_responses (workspace_id, response_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_narrative_deliveries_active_idx
    ON omnix_rpg_narrative_deliveries (
        workspace_id, status, updated_at, response_id
    )
    WHERE status IN ('pending', 'streaming');

COMMENT ON TABLE omnix_rpg_narrative_deliveries IS
    'Mutable delivery cursors for immutable canonical narrative responses. Reconnect and mode changes reuse response_id plus semantic_hash without regenerating prose.';
