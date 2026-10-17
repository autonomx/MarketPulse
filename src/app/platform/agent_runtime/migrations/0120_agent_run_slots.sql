-- omnix-migration: phase=expand transactional=true
-- WP-4.7: at most OMNIX_AGENT_MAX_CONCURRENT_RUNS agent processes run at once,
-- across every Omnix process. A process takes a slot under the singleton lock
-- row (SELECT ... FOR UPDATE), renews its lease while the agent runs and
-- deletes it when the agent exits; a slot whose holder died expires.
-- Capacity is the machine's, so neither table is per workspace.
CREATE TABLE IF NOT EXISTS omnix_agent_run_slot_lock (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton)
);
INSERT INTO omnix_agent_run_slot_lock (singleton) VALUES (TRUE) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS omnix_agent_run_slots (
    run_id TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_expires_at TIMESTAMPTZ NOT NULL
);
