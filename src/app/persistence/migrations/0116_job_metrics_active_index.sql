-- omnix-migration: phase=expand transactional=false
-- WP-10.3: /metrics reads the active jobs of a workspace by type and status
-- on every scrape. Active jobs are a small share of the table. Without this
-- partial index the read scans every finished job.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_omnix_jobs_workspace_active
    ON omnix_jobs (workspace_id, job_type, status)
    WHERE status IN ('queued', 'leased', 'running', 'waiting', 'retrying', 'cancel_requested')
