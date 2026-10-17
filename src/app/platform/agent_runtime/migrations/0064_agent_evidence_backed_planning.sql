-- Evidence-backed durable planning and pre-implementation mutation authority.

CREATE TABLE IF NOT EXISTS omnix_agent_planning_state (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'shadow',
    task_revision_id TEXT,
    status TEXT NOT NULL DEFAULT 'required',
    latest_plan_revision_id TEXT,
    active_plan_revision_id TEXT,
    planning_baseline_id TEXT,
    baseline_provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    CHECK (mode IN ('off','shadow','enforce')),
    CHECK (status IN ('required','submitted','approved','rejected','stale','invalid'))
);

CREATE TABLE IF NOT EXISTS omnix_agent_inspection_evidence (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    task_revision_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT,
    completeness TEXT NOT NULL,
    result_digest TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, evidence_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    CHECK (completeness IN ('complete','truncated','paginated','partial','unknown'))
);
CREATE INDEX IF NOT EXISTS idx_omnix_agent_inspection_evidence_revision
    ON omnix_agent_inspection_evidence
       (workspace_id, run_id, task_revision_id, created_at, evidence_id);

CREATE TABLE IF NOT EXISTS omnix_agent_impact_candidates (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    task_revision_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    path TEXT NOT NULL,
    relation TEXT NOT NULL,
    impact_likelihood TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, candidate_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    CHECK (impact_likelihood IN ('low','medium','high'))
);
CREATE INDEX IF NOT EXISTS idx_omnix_agent_impact_candidates_revision
    ON omnix_agent_impact_candidates
       (workspace_id, run_id, task_revision_id, impact_likelihood, path);

CREATE TABLE IF NOT EXISTS omnix_agent_plan_revisions (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    task_revision_id TEXT NOT NULL,
    plan_revision_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    previous_plan_revision_id TEXT,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    planning_baseline_id TEXT NOT NULL,
    inspection_evidence_digest TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, plan_revision_id),
    UNIQUE (workspace_id, run_id, task_revision_id, sequence),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    CHECK (source IN ('initial','delta','repair')),
    CHECK (status IN ('required','submitted','approved','rejected','stale','invalid')),
    CHECK (mode IN ('off','shadow','enforce'))
);
CREATE INDEX IF NOT EXISTS idx_omnix_agent_plan_revisions_active
    ON omnix_agent_plan_revisions
       (workspace_id, run_id, task_revision_id, status, sequence DESC);

CREATE TABLE IF NOT EXISTS omnix_agent_planning_decisions (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    task_revision_id TEXT,
    plan_revision_id TEXT,
    mode TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    effect TEXT NOT NULL,
    target TEXT,
    allowed BOOLEAN NOT NULL,
    would_block BOOLEAN NOT NULL DEFAULT FALSE,
    reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, decision_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE,
    CHECK (mode IN ('off','shadow','enforce')),
    CHECK (effect IN ('read','validate','mutate','external_mutate','unknown'))
);
CREATE INDEX IF NOT EXISTS idx_omnix_agent_planning_decisions_metrics
    ON omnix_agent_planning_decisions
       (workspace_id, mode, would_block, effect, created_at DESC);
