CREATE TABLE IF NOT EXISTS omnix_rpg_narrative_retirement_records (
    workspace_id TEXT NOT NULL,
    response_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    publisher TEXT NOT NULL,
    canonical_publish_count BIGINT NOT NULL DEFAULT 0 CHECK (canonical_publish_count >= 0),
    alternate_publish_count BIGINT NOT NULL DEFAULT 0 CHECK (alternate_publish_count >= 0),
    rejected_alternate_count BIGINT NOT NULL DEFAULT 0 CHECK (rejected_alternate_count >= 0),
    legacy_ownership_retired BOOLEAN NOT NULL DEFAULT FALSE,
    compatibility_projection_only BOOLEAN NOT NULL DEFAULT FALSE,
    delivery_mode TEXT NOT NULL DEFAULT 'blocking',
    production_certification_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    deletion_audit_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, response_id),
    FOREIGN KEY (workspace_id, response_id)
        REFERENCES omnix_rpg_narrative_responses (workspace_id, response_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_narrative_retirement_release_idx
    ON omnix_rpg_narrative_retirement_records (
        workspace_id,
        legacy_ownership_retired,
        compatibility_projection_only,
        alternate_publish_count,
        updated_at
    );

COMMENT ON TABLE omnix_rpg_narrative_retirement_records IS
    'Per-response proof that canonical publication owns visible RPG prose, alternate publishers remain zero, compatibility fields are projections only, and retired production hooks are absent.';
