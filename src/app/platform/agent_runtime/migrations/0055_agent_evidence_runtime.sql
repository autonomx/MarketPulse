-- Agent evidence policy, immutable task revisions, receipts, and superseding runs.

ALTER TABLE omnix_agent_runs
    ADD COLUMN IF NOT EXISTS supersedes_run_id TEXT,
    ADD COLUMN IF NOT EXISTS superseded_by_run_id TEXT;

CREATE INDEX IF NOT EXISTS idx_omnix_agent_runs_supersedes
    ON omnix_agent_runs (workspace_id, supersedes_run_id)
    WHERE supersedes_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_omnix_agent_runs_superseded_by
    ON omnix_agent_runs (workspace_id, superseded_by_run_id)
    WHERE superseded_by_run_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS omnix_agent_task_revisions (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    sequence BIGINT NOT NULL CHECK (sequence >= 1),
    previous_revision_id TEXT,
    source_command_id TEXT,
    user_instruction TEXT NOT NULL,
    effective_objective TEXT NOT NULL,
    effective_success_criteria JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_decision JSONB NOT NULL DEFAULT '{}'::jsonb,
    required_local_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    required_external_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    expected_artifacts JSONB NOT NULL DEFAULT '[]'::jsonb,
    acceptance_checks JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, revision_id),
    UNIQUE (workspace_id, run_id, sequence),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_omnix_agent_task_revision_command
    ON omnix_agent_task_revisions (workspace_id, run_id, source_command_id)
    WHERE source_command_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS omnix_agent_evidence_receipts (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    task_revision_id TEXT,
    capability_id TEXT NOT NULL,
    source_class TEXT NOT NULL,
    subject JSONB,
    request_digest TEXT NOT NULL,
    provider TEXT,
    origin TEXT,
    source_manifest_id TEXT,
    source_count INTEGER NOT NULL DEFAULT 0 CHECK (source_count >= 0),
    executed_at TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    freshest_source_at TIMESTAMPTZ,
    trust_level TEXT NOT NULL,
    result_digest TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, receipt_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_evidence_receipts_source
    ON omnix_agent_evidence_receipts (workspace_id, run_id, source_class, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_omnix_agent_evidence_receipts_revision
    ON omnix_agent_evidence_receipts (workspace_id, run_id, task_revision_id)
    WHERE task_revision_id IS NOT NULL;
