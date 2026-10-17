-- omnix-migration: phase=expand transactional=true
-- WP-5.4: commit-safe event cursor.
--
-- tx_id records the writing transaction. Readers deliver only rows whose
-- transaction is older than every transaction still in progress
-- (tx_id < pg_snapshot_xmin(pg_current_snapshot())) and order by
-- (tx_id, id), so a lower id that commits late is never skipped.
-- Existing rows get 0: they were committed long ago.

ALTER TABLE omnix_job_events ADD COLUMN IF NOT EXISTS tx_id xid8 NOT NULL DEFAULT '0'::xid8;
ALTER TABLE omnix_job_events ALTER COLUMN tx_id SET DEFAULT pg_current_xact_id();

CREATE INDEX IF NOT EXISTS idx_omnix_job_events_commit_order
    ON omnix_job_events (workspace_id, tx_id, id);
