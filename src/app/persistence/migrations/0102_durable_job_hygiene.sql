-- omnix-migration: phase=expand transactional=true
UPDATE omnix_jobs
   SET status = 'canceled'
 WHERE status = 'cancelled';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'ck_omnix_jobs_status'
           AND conrelid = 'omnix_jobs'::regclass
    ) THEN
        ALTER TABLE omnix_jobs
            ADD CONSTRAINT ck_omnix_jobs_status
            CHECK (status IN (
                'queued', 'leased', 'running', 'waiting', 'retrying',
                'completed', 'failed', 'cancel_requested', 'paused',
                'canceled', 'stale'
            )) NOT VALID;
    END IF;
END
$$;

ALTER TABLE omnix_jobs VALIDATE CONSTRAINT ck_omnix_jobs_status;

CREATE TABLE IF NOT EXISTS omnix_job_logs (
    job_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    seq BIGINT NOT NULL CHECK (seq >= 1),
    level TEXT,
    message TEXT,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (job_id, seq),
    CONSTRAINT fk_omnix_job_logs_workspace_job
        FOREIGN KEY (workspace_id, job_id)
        REFERENCES omnix_jobs(workspace_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_job_logs_workspace_job_seq
    ON omnix_job_logs (workspace_id, job_id, seq);

INSERT INTO omnix_job_logs (
    job_id, workspace_id, seq, level, message, data, created_at
)
SELECT jobs.id,
       jobs.workspace_id,
       entry.ordinality,
       CASE WHEN jsonb_typeof(entry.item) = 'object'
            THEN entry.item ->> 'level' ELSE 'info' END,
       CASE WHEN jsonb_typeof(entry.item) = 'object'
            THEN entry.item ->> 'message' ELSE entry.item::text END,
       CASE WHEN jsonb_typeof(entry.item) = 'object'
            THEN entry.item - 'level' - 'message'
            ELSE jsonb_build_object('legacy_value', entry.item)
       END,
       jobs.updated_at
  FROM omnix_jobs AS jobs
 CROSS JOIN LATERAL jsonb_array_elements(
       CASE WHEN jsonb_typeof(jobs.metadata #> '{compat_contract,logs}') = 'array'
            THEN jobs.metadata #> '{compat_contract,logs}'
            ELSE '[]'::jsonb
       END
  ) WITH ORDINALITY AS entry(item, ordinality)
ON CONFLICT (job_id, seq) DO NOTHING;

UPDATE omnix_jobs
   SET metadata = metadata #- '{compat_contract,logs}'
 WHERE metadata #> '{compat_contract,logs}' IS NOT NULL;
