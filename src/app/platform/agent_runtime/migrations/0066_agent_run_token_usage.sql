-- Track both sides of provider-reported model token usage for durable run cards.
-- The counters remain PostgreSQL-authoritative so worker restarts cannot reset them.

ALTER TABLE omnix_agent_run_usage
    ADD COLUMN IF NOT EXISTS input_tokens BIGINT NOT NULL DEFAULT 0
    CHECK (input_tokens >= 0);
