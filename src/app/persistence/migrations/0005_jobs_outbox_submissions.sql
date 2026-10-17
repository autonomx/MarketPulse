CREATE TABLE IF NOT EXISTS omnix_jobs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    module TEXT NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    resource_class TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    progress JSONB NOT NULL DEFAULT '{}'::jsonb,
    error JSONB,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TIMESTAMPTZ,
    cancel_requested_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_omnix_jobs_claim
    ON omnix_jobs (status, available_at, priority DESC, created_at, id)
    WHERE status IN ('queued', 'retrying', 'waiting');

CREATE INDEX IF NOT EXISTS idx_omnix_jobs_lease_expiry
    ON omnix_jobs (lease_expires_at, status)
    WHERE lease_expires_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_omnix_jobs_workspace_created
    ON omnix_jobs (workspace_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS omnix_job_attempts (
    job_id TEXT NOT NULL REFERENCES omnix_jobs(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    worker_id TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    error JSONB,
    PRIMARY KEY (job_id, attempt)
);

CREATE TABLE IF NOT EXISTS omnix_job_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES omnix_jobs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_omnix_job_events_job
    ON omnix_job_events (workspace_id, job_id, id);

CREATE TABLE IF NOT EXISTS omnix_dead_letters (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES omnix_jobs(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS omnix_outbox_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    ordering_key TEXT,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    claimed_by TEXT,
    claim_token TEXT,
    claim_expires_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_omnix_outbox_claim
    ON omnix_outbox_events (status, available_at, id)
    WHERE status IN ('pending', 'retrying');

CREATE TABLE IF NOT EXISTS omnix_rpg_foreground_submissions (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'claimed',
    claim_token TEXT NOT NULL,
    job_id TEXT REFERENCES omnix_jobs(id) ON DELETE SET NULL,
    interaction_id TEXT,
    response JSONB,
    error TEXT,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    execution_started_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, session_id, submission_id)
);

CREATE INDEX IF NOT EXISTS idx_omnix_rpg_submissions_status
    ON omnix_rpg_foreground_submissions (status, lease_expires_at, updated_at);
