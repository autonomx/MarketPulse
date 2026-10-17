CREATE TABLE IF NOT EXISTS omnix_rpg_item_descriptions (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    description_key TEXT NOT NULL CHECK (length(description_key) = 64),
    item_key TEXT NOT NULL,
    item_name TEXT NOT NULL,
    genre TEXT NOT NULL,
    context_hash TEXT NOT NULL CHECK (length(context_hash) = 64),
    summary TEXT NOT NULL CHECK (length(trim(summary)) > 0),
    source TEXT NOT NULL DEFAULT 'llm',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, description_key)
);

CREATE INDEX IF NOT EXISTS idx_omnix_rpg_item_descriptions_item
    ON omnix_rpg_item_descriptions (workspace_id, item_key, updated_at DESC);

COMMENT ON TABLE omnix_rpg_item_descriptions IS
    'Persistent presentation-only RPG item descriptions keyed by canonical item and setting context.';
