-- omnix-migration: phase=expand transactional=false
-- WP-7.4: typed event reads (the latest event of a type, a run's tool or
-- message events) use this index instead of reading up to 5,000 events.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_omnix_agent_events_type
    ON omnix_agent_run_events (workspace_id, run_id, event_type, sequence)
