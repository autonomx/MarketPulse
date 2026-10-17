CREATE TABLE IF NOT EXISTS omnix_rpg_campaign_bibles (
    workspace_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    document_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_hash TEXT NOT NULL,
    provenance_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    consistency_report_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    completeness_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, campaign_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_rpg_campaign_bible_revisions (
    workspace_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    document_jsonb JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    provenance_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    consistency_report_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    completeness_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, campaign_id, revision),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaign_bibles (workspace_id, campaign_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_campaign_bible_revisions_lookup_idx
    ON omnix_rpg_campaign_bible_revisions (workspace_id, campaign_id, revision DESC);

COMMENT ON TABLE omnix_rpg_campaign_bibles IS
    'Authoritative revisioned Campaign Bible aggregate for one RPG campaign.';
COMMENT ON TABLE omnix_rpg_campaign_bible_revisions IS
    'Append-only Campaign Bible revision history for auditing and replay.';
