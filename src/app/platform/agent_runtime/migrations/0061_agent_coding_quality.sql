-- Durable coding-quality controller state and exact-state evidence.
-- This state is intentionally separate from omnix_agent_runs.status so run
-- lifecycle and coding-quality recovery can advance independently.

ALTER TABLE omnix_agent_task_revisions
    ADD COLUMN IF NOT EXISTS requirements JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS constraints JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS validation_plan JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS omnix_agent_coding_quality_state (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    task_revision_id TEXT,
    workspace_state_id TEXT,
    stage_started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    CHECK (stage IN (
        'inspect','planning','implementing','self_review','validating',
        'reviewing','repairing','acceptance'
    ))
);

CREATE TABLE IF NOT EXISTS omnix_agent_workspace_states (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    state_id TEXT NOT NULL,
    task_revision_id TEXT,
    base_commit_sha TEXT NOT NULL,
    tracked_diff_sha256 TEXT NOT NULL,
    untracked_file_manifest_sha256 TEXT NOT NULL,
    modified_paths JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, state_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_workspace_states_revision
    ON omnix_agent_workspace_states (workspace_id, run_id, task_revision_id, created_at DESC);

CREATE TABLE IF NOT EXISTS omnix_agent_validation_results (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    result_id TEXT NOT NULL,
    validation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    task_revision_id TEXT,
    workspace_state_id TEXT NOT NULL,
    command TEXT NOT NULL,
    exit_code INTEGER,
    success BOOLEAN NOT NULL,
    output_digest TEXT NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (workspace_id, run_id, result_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, run_id, workspace_state_id)
        REFERENCES omnix_agent_workspace_states(workspace_id, run_id, state_id) ON DELETE CASCADE,
    CHECK (kind IN ('test','typecheck','lint','build','diff_review','custom'))
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_validation_state
    ON omnix_agent_validation_results (
        workspace_id, run_id, task_revision_id, workspace_state_id, validation_id, success
    );

CREATE TABLE IF NOT EXISTS omnix_agent_review_snapshots (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    task_revision_id TEXT,
    workspace_state_id TEXT NOT NULL,
    base_commit_sha TEXT NOT NULL,
    patch_checksum TEXT NOT NULL,
    patch_storage_ref TEXT,
    workspace_root TEXT NOT NULL,
    relevant_files JSONB NOT NULL DEFAULT '[]'::jsonb,
    validation_result_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    repository_guidance_digest TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, snapshot_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, run_id, workspace_state_id)
        REFERENCES omnix_agent_workspace_states(workspace_id, run_id, state_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_review_snapshot_state
    ON omnix_agent_review_snapshots (
        workspace_id, run_id, task_revision_id, workspace_state_id, created_at DESC
    );

CREATE TABLE IF NOT EXISTS omnix_agent_review_results (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    review_result_id TEXT NOT NULL,
    reviewer_run_id TEXT NOT NULL,
    review_snapshot_id TEXT NOT NULL,
    task_revision_id TEXT,
    workspace_state_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    requirements JSONB NOT NULL DEFAULT '[]'::jsonb,
    findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_tests JSONB NOT NULL DEFAULT '[]'::jsonb,
    residual_risks JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, review_result_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, run_id, review_snapshot_id)
        REFERENCES omnix_agent_review_snapshots(workspace_id, run_id, snapshot_id) ON DELETE CASCADE,
    CHECK (verdict IN ('approve','changes_required','blocked'))
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_review_result_state
    ON omnix_agent_review_results (
        workspace_id, run_id, task_revision_id, workspace_state_id, verdict, created_at DESC
    );
