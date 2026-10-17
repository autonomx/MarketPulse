CREATE TABLE omnix_capability_approvals (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('tool_proposal', 'agent_request')),
    subject_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    proposal_digest TEXT NOT NULL CHECK (proposal_digest ~ '^[0-9a-f]{64}$'),
    proposal_payload JSONB NOT NULL,
    approval_required BOOLEAN NOT NULL,
    requested_by TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'pending'
        CHECK (decision IN ('pending', 'approved', 'denied', 'expired', 'consumed')),
    decided_by TEXT,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    UNIQUE (workspace_id, id),
    CHECK (expires_at > created_at),
    CHECK ((decision = 'consumed') = (consumed_at IS NOT NULL))
);

CREATE INDEX idx_omnix_capability_approvals_pending
    ON omnix_capability_approvals (workspace_id, expires_at, id)
    WHERE decision IN ('pending', 'approved');
