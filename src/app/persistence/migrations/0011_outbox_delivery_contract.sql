CREATE SEQUENCE IF NOT EXISTS omnix_outbox_event_key_seq;

ALTER TABLE omnix_outbox_events
    ADD COLUMN IF NOT EXISTS event_key TEXT,
    ADD COLUMN IF NOT EXISTS schema_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS correlation_id TEXT,
    ADD COLUMN IF NOT EXISTS causation_id TEXT,
    ADD COLUMN IF NOT EXISTS aggregate_sequence BIGINT,
    ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN IF NOT EXISTS publication_attempted_at TIMESTAMPTZ;

UPDATE omnix_outbox_events
   SET event_key = 'legacy:' || id::text
 WHERE event_key IS NULL;

ALTER TABLE omnix_outbox_events
    ALTER COLUMN event_key SET DEFAULT (
        'event:' || nextval('omnix_outbox_event_key_seq')::text
    ),
    ALTER COLUMN event_key SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_omnix_outbox_event_key
    ON omnix_outbox_events (event_key);

CREATE UNIQUE INDEX IF NOT EXISTS uq_omnix_outbox_ordering_sequence
    ON omnix_outbox_events (workspace_id, ordering_key, aggregate_sequence)
    WHERE ordering_key IS NOT NULL AND aggregate_sequence IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_omnix_outbox_ordered_pending
    ON omnix_outbox_events (workspace_id, ordering_key, aggregate_sequence, id)
    WHERE status <> 'published';

CREATE TABLE IF NOT EXISTS omnix_outbox_sequences (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    ordering_key TEXT NOT NULL,
    next_sequence BIGINT NOT NULL DEFAULT 1 CHECK (next_sequence >= 1),
    PRIMARY KEY (workspace_id, ordering_key)
);

INSERT INTO omnix_outbox_sequences (workspace_id, ordering_key, next_sequence)
SELECT workspace_id, ordering_key, COALESCE(MAX(aggregate_sequence), MAX(id), 0) + 1
  FROM omnix_outbox_events
 WHERE ordering_key IS NOT NULL
 GROUP BY workspace_id, ordering_key
ON CONFLICT (workspace_id, ordering_key) DO UPDATE
SET next_sequence = GREATEST(
    omnix_outbox_sequences.next_sequence,
    EXCLUDED.next_sequence
);

CREATE TABLE IF NOT EXISTS omnix_outbox_consumer_inbox (
    consumer_id TEXT NOT NULL,
    event_key TEXT NOT NULL REFERENCES omnix_outbox_events(event_key) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'completed', 'failed', 'dead_letter')),
    claim_token TEXT,
    claim_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error TEXT,
    result JSONB,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (consumer_id, event_key)
);

CREATE INDEX IF NOT EXISTS idx_omnix_outbox_inbox_recovery
    ON omnix_outbox_consumer_inbox (status, claim_expires_at, updated_at)
    WHERE status IN ('processing', 'failed');

CREATE TABLE IF NOT EXISTS omnix_outbox_dead_letters (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    consumer_id TEXT NOT NULL,
    event_key TEXT NOT NULL REFERENCES omnix_outbox_events(event_key) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ,
    UNIQUE (consumer_id, event_key)
);

CREATE TABLE IF NOT EXISTS omnix_side_effect_receipts (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    effect_scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'reserved'
        CHECK (status IN ('reserved', 'completed', 'failed')),
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, effect_scope, idempotency_key)
);
