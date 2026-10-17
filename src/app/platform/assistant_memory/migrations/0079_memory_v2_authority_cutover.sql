ALTER TABLE omnix_memory_v2_authority_streams
    ADD COLUMN IF NOT EXISTS authoritative_event_watermark BIGINT NOT NULL DEFAULT 0
        CHECK (authoritative_event_watermark >= 0);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_cutover_readiness_receipts (
    receipt_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    authoritative_event_watermark BIGINT NOT NULL CHECK (authoritative_event_watermark >= 0),
    observation_watermark BIGINT NOT NULL CHECK (observation_watermark >= 0),
    consolidation_watermark BIGINT NOT NULL CHECK (consolidation_watermark >= 0),
    graph_revision BIGINT NOT NULL CHECK (graph_revision >= 0),
    graph_source_observation_watermark BIGINT NOT NULL CHECK (graph_source_observation_watermark >= 0),
    index_graph_revision BIGINT NOT NULL CHECK (index_graph_revision >= 0),
    index_source_observation_watermark BIGINT NOT NULL CHECK (index_source_observation_watermark >= 0),
    index_governance_digest TEXT NOT NULL,
    graph_validation_passed BOOLEAN NOT NULL,
    graph_validation_digest TEXT,
    shadow_evaluation_id TEXT,
    shadow_quality_passed BOOLEAN NOT NULL,
    indexes_caught_up BOOLEAN NOT NULL,
    ready BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE,
    FOREIGN KEY (shadow_evaluation_id)
        REFERENCES omnix_memory_v2_shadow_evaluations(evaluation_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_cutover_receipts_space_time
    ON omnix_memory_v2_cutover_readiness_receipts(
        principal_id, owner_type, owner_id, created_at DESC
    );

CREATE TABLE IF NOT EXISTS omnix_memory_v2_authority_epochs (
    epoch BIGINT PRIMARY KEY CHECK (epoch >= 1),
    authority TEXT NOT NULL CHECK (authority IN ('v1', 'v2')),
    activated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    previous_epoch BIGINT,
    readiness JSONB,
    readiness_receipt_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    activated_by TEXT NOT NULL,
    reason TEXT,
    FOREIGN KEY (previous_epoch)
        REFERENCES omnix_memory_v2_authority_epochs(epoch)
        ON DELETE RESTRICT
);

INSERT INTO omnix_memory_v2_authority_epochs (
    epoch, authority, activated_at, previous_epoch, readiness,
    readiness_receipt_ids, activated_by, reason
) VALUES (
    1, 'v1', CURRENT_TIMESTAMP, NULL, NULL, '[]'::jsonb,
    'migration:memory-v2-phase15', 'Initial legacy memory authority epoch'
)
ON CONFLICT (epoch) DO NOTHING;

CREATE TABLE IF NOT EXISTS omnix_memory_v2_authority_current (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    current_epoch BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (current_epoch)
        REFERENCES omnix_memory_v2_authority_epochs(epoch)
        ON DELETE RESTRICT
);

INSERT INTO omnix_memory_v2_authority_current (singleton, current_epoch)
VALUES (TRUE, 1)
ON CONFLICT (singleton) DO NOTHING;
