CREATE TABLE IF NOT EXISTS omnix_rpg_hermes_research (
    workspace_id TEXT NOT NULL,
    research_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    request_jsonb JSONB NOT NULL,
    result_jsonb JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'complete'
        CHECK (status IN ('complete', 'rejected', 'failed')),
    source_count INTEGER NOT NULL DEFAULT 0 CHECK (source_count >= 0),
    finding_count INTEGER NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, research_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_hermes_research_campaign_idx
    ON omnix_rpg_hermes_research (
        workspace_id, campaign_id, created_at DESC, research_id DESC
    );

COMMENT ON TABLE omnix_rpg_hermes_research IS
    'Bounded, read-only Hermes narrative research with source provenance; never direct Campaign Bible authority.';
