CREATE TABLE IF NOT EXISTS omnix_memory_v2_search_index_state (
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    index_graph_revision BIGINT NOT NULL DEFAULT 0 CHECK (index_graph_revision >= 0),
    source_observation_watermark BIGINT NOT NULL DEFAULT 0 CHECK (source_observation_watermark >= 0),
    governance_digest TEXT NOT NULL,
    projection_digest TEXT NOT NULL,
    entry_count BIGINT NOT NULL DEFAULT 0 CHECK (entry_count >= 0),
    built_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (principal_id, owner_type, owner_id),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_search_index_entries (
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    item_type TEXT NOT NULL CHECK (item_type IN ('assertion')),
    ref_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    content TEXT NOT NULL,
    visibility_scopes JSONB NOT NULL,
    evidence_observation_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_revision BIGINT NOT NULL CHECK (source_revision >= 1),
    graph_revision BIGINT NOT NULL CHECK (graph_revision >= 1),
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple', COALESCE(content, ''))
    ) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (principal_id, owner_type, owner_id, item_type, ref_id),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_search_index_state(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_search_entries_vector
    ON omnix_memory_v2_search_index_entries USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_memory_v2_search_entries_space_domain
    ON omnix_memory_v2_search_index_entries(
        principal_id, owner_type, owner_id, domain
    );
