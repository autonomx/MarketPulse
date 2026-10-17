-- Final coding-quality hardening.
ALTER TABLE omnix_agent_validation_results
    ADD COLUMN IF NOT EXISTS covers_requirement_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE TABLE IF NOT EXISTS omnix_agent_self_review_results (
    workspace_id TEXT NOT NULL, run_id TEXT NOT NULL, self_review_result_id TEXT NOT NULL,
    task_revision_id TEXT, workspace_state_id TEXT NOT NULL, verdict TEXT NOT NULL,
    requirements JSONB NOT NULL DEFAULT '[]'::jsonb, findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_tests JSONB NOT NULL DEFAULT '[]'::jsonb, residual_risks JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, self_review_result_id),
    FOREIGN KEY (workspace_id, run_id) REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, run_id, workspace_state_id) REFERENCES omnix_agent_workspace_states(workspace_id, run_id, state_id) ON DELETE CASCADE,
    CHECK (verdict IN ('approve','changes_required','blocked'))
);
CREATE INDEX IF NOT EXISTS idx_omnix_agent_self_review_state ON omnix_agent_self_review_results
    (workspace_id, run_id, task_revision_id, workspace_state_id, verdict, created_at DESC);
