CREATE TABLE IF NOT EXISTS omnix_rpg_world_forge_proposals (
    workspace_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    base_bible_revision BIGINT NOT NULL CHECK (base_bible_revision >= 0),
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'rejected')),
    proposal_jsonb JSONB NOT NULL,
    consistency_report_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    proposed_by TEXT NOT NULL DEFAULT 'world_forge',
    decision_note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, proposal_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_world_forge_campaign_status_idx
    ON omnix_rpg_world_forge_proposals (
        workspace_id, campaign_id, status, created_at DESC
    );

COMMENT ON TABLE omnix_rpg_world_forge_proposals IS
    'Reviewable World Forge proposals; only approved proposals may revise the Campaign Bible.';
