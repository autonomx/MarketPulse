CREATE TABLE IF NOT EXISTS omnix_retention_policies (
    record_type TEXT PRIMARY KEY,
    retention_days INTEGER NOT NULL CHECK (retention_days >= 1),
    terminal_only BOOLEAN NOT NULL DEFAULT TRUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

INSERT INTO omnix_retention_policies (record_type, retention_days, terminal_only)
VALUES
    ('outbox_events', 30, TRUE),
    ('outbox_consumer_inbox', 30, TRUE),
    ('outbox_dead_letters', 90, TRUE),
    ('job_events', 90, FALSE),
    ('audit_events', 365, FALSE),
    ('runtime_failure_evidence', 30, FALSE)
ON CONFLICT (record_type) DO NOTHING;

CREATE TABLE IF NOT EXISTS omnix_capacity_policy (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    max_outbox_payload_bytes INTEGER NOT NULL DEFAULT 1048576
        CHECK (max_outbox_payload_bytes BETWEEN 1024 AND 16777216),
    max_jsonb_record_bytes INTEGER NOT NULL DEFAULT 8388608
        CHECK (max_jsonb_record_bytes BETWEEN 1024 AND 67108864),
    disk_warning_percent INTEGER NOT NULL DEFAULT 80
        CHECK (disk_warning_percent BETWEEN 1 AND 99),
    disk_hard_stop_percent INTEGER NOT NULL DEFAULT 95
        CHECK (disk_hard_stop_percent BETWEEN 2 AND 100),
    cleanup_batch_size INTEGER NOT NULL DEFAULT 1000
        CHECK (cleanup_batch_size BETWEEN 1 AND 100000),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (disk_warning_percent < disk_hard_stop_percent)
);

INSERT INTO omnix_capacity_policy (singleton)
VALUES (TRUE)
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS omnix_lifecycle_cleanup_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    deleted_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    capacity_before JSONB NOT NULL DEFAULT '{}'::jsonb,
    capacity_after JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT
);

ALTER TABLE omnix_outbox_events
    ADD CONSTRAINT ck_omnix_outbox_payload_capacity
    CHECK (pg_column_size(payload) <= 1048576) NOT VALID;

ALTER TABLE omnix_outbox_events
    VALIDATE CONSTRAINT ck_omnix_outbox_payload_capacity;

CREATE INDEX IF NOT EXISTS idx_omnix_outbox_terminal_retention
    ON omnix_outbox_events (published_at, id)
    WHERE status = 'published';

CREATE INDEX IF NOT EXISTS idx_omnix_consumer_inbox_terminal_retention
    ON omnix_outbox_consumer_inbox (updated_at, consumer_id, event_key)
    WHERE status IN ('completed', 'dead_letter');

CREATE INDEX IF NOT EXISTS idx_omnix_dead_letters_resolved_retention
    ON omnix_outbox_dead_letters (resolved_at, id)
    WHERE resolved_at IS NOT NULL;
