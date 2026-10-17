CREATE TABLE IF NOT EXISTS omnix_trading_research_shadow_annotations (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    annotation_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    novelty TEXT NOT NULL,
    relevance TEXT NOT NULL,
    catalyst_class TEXT NOT NULL,
    conflict_summary TEXT NOT NULL DEFAULT '',
    confidence NUMERIC NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    rationale TEXT NOT NULL DEFAULT '',
    shadow_only BOOLEAN NOT NULL DEFAULT TRUE CHECK (shadow_only = TRUE),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, annotation_id)
);
CREATE INDEX IF NOT EXISTS idx_omnix_trading_research_shadow_asof
    ON omnix_trading_research_shadow_annotations (workspace_id, instrument_id, observed_at DESC);
