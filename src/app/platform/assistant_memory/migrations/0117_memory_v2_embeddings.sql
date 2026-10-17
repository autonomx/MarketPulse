-- omnix-migration: phase=expand transactional=true
-- Memory v2 embedding retrieval (VoiceMem's multilingual-e5-small, owner
-- decision). One 384-dimension float32 vector per distinct searchable text of
-- a memory space and model, keyed by the text's SHA-256 so index rebuilds
-- reuse unchanged vectors. Vectors are compared in the application, so no
-- database extension is needed. Rows go with the space's search index.
CREATE TABLE IF NOT EXISTS omnix_memory_v2_embeddings (
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    content_digest TEXT NOT NULL CHECK (char_length(content_digest) = 64),
    embedding BYTEA NOT NULL CHECK (octet_length(embedding) = 1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (principal_id, owner_type, owner_id, model_id, content_digest),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_search_index_state(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);
