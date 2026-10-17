CREATE TABLE IF NOT EXISTS omnix_rpg_narrative_responses (
    workspace_id TEXT NOT NULL,
    response_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    content_hash TEXT NOT NULL,
    canonical_jsonb JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, response_id),
    UNIQUE (workspace_id, campaign_id, turn_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_narrative_responses_campaign_idx
    ON omnix_rpg_narrative_responses (
        workspace_id, campaign_id, revision, turn_id, response_id
    );

COMMENT ON TABLE omnix_rpg_narrative_responses IS
    'Immutable canonical narrative responses used by save/load, replay, and delivery certification.';
