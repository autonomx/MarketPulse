-- Durable evidence retrieval reservations and revision referential integrity.

ALTER TABLE omnix_agent_evidence_receipts
    ADD CONSTRAINT fk_omnix_agent_evidence_receipt_revision
    FOREIGN KEY (workspace_id, run_id, task_revision_id)
    REFERENCES omnix_agent_task_revisions(workspace_id, run_id, revision_id)
    ON DELETE CASCADE;

CREATE TABLE omnix_agent_evidence_query_reservations (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    task_revision_id TEXT NOT NULL,
    execution_key TEXT NOT NULL,
    reserved_sources INTEGER NOT NULL DEFAULT 0 CHECK (reserved_sources >= 0),
    reserved_extracts INTEGER NOT NULL DEFAULT 0 CHECK (reserved_extracts >= 0),
    actual_sources INTEGER NOT NULL DEFAULT 0 CHECK (actual_sources >= 0),
    actual_extracts INTEGER NOT NULL DEFAULT 0 CHECK (actual_extracts >= 0),
    state TEXT NOT NULL DEFAULT 'reserved'
        CHECK (state IN ('reserved','completed','failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, task_revision_id, execution_key),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, run_id, task_revision_id)
        REFERENCES omnix_agent_task_revisions(workspace_id, run_id, revision_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_omnix_agent_evidence_query_reservations_revision
    ON omnix_agent_evidence_query_reservations (
        workspace_id, run_id, task_revision_id, created_at
    );
