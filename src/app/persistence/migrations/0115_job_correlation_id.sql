-- omnix-migration: phase=expand transactional=true
-- WP-10.2: the request id that submitted a job. Workers bind it to their log
-- lines, so one id follows a submission from the HTTP request into the job.
-- Nullable: jobs submitted outside a request (scheduler, CLI) have none.

ALTER TABLE omnix_jobs ADD COLUMN IF NOT EXISTS correlation_id text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'ck_omnix_jobs_correlation_id'
           AND conrelid = 'omnix_jobs'::regclass
    ) THEN
        ALTER TABLE omnix_jobs
            ADD CONSTRAINT ck_omnix_jobs_correlation_id
            CHECK (correlation_id IS NULL OR char_length(correlation_id) <= 128) NOT VALID;
    END IF;
END
$$;

ALTER TABLE omnix_jobs VALIDATE CONSTRAINT ck_omnix_jobs_correlation_id;
