-- omnix-migration: phase=expand transactional=true
-- WP-7.4: a per-run event counter. Appending an event locked the run row
-- (SELECT ... FOR UPDATE) to compute MAX(sequence) + 1; the counter row now
-- orders one run's appends without blocking updates to the run itself.
CREATE TABLE IF NOT EXISTS omnix_agent_run_event_counters (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    last_sequence BIGINT NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    PRIMARY KEY (workspace_id, run_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_agent_runs(workspace_id, run_id) ON DELETE CASCADE
);

INSERT INTO omnix_agent_run_event_counters (workspace_id, run_id, last_sequence)
SELECT workspace_id, run_id, MAX(sequence)
  FROM omnix_agent_run_events
 GROUP BY workspace_id, run_id
ON CONFLICT (workspace_id, run_id) DO UPDATE
   SET last_sequence = GREATEST(omnix_agent_run_event_counters.last_sequence, EXCLUDED.last_sequence);

ALTER TABLE omnix_agent_run_event_counters ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_agent_run_event_counters FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_agent_run_event_counters;
CREATE POLICY tenant_isolation ON omnix_agent_run_event_counters
    USING (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on')
    WITH CHECK (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on');
