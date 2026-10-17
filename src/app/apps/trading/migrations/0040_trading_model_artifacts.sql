CREATE TABLE IF NOT EXISTS omnix_trading_model_artifacts (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    label_definition TEXT NOT NULL,
    trained_at TIMESTAMPTZ NOT NULL,
    training_examples INTEGER NOT NULL CHECK (training_examples > 0),
    positive_examples INTEGER NOT NULL CHECK (positive_examples >= 0),
    shadow_only BOOLEAN NOT NULL DEFAULT TRUE CHECK (shadow_only = TRUE),
    artifact JSONB NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, model_id, model_version),
    UNIQUE (workspace_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_model_artifacts_recent
    ON omnix_trading_model_artifacts (workspace_id, model_id, trained_at DESC);
