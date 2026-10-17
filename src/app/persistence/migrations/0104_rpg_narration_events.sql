-- omnix-migration: phase=expand transactional=true
CREATE TABLE IF NOT EXISTS omnix_rpg_narration_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id TEXT NOT NULL CHECK (length(session_id) BETWEEN 1 AND 256),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_omnix_rpg_narration_event_payload_capacity
        CHECK (pg_column_size(payload) <= 65536)
);

CREATE INDEX IF NOT EXISTS idx_omnix_rpg_narration_events_session_id
    ON omnix_rpg_narration_events (session_id, event_id);

CREATE INDEX IF NOT EXISTS idx_omnix_rpg_narration_events_retention
    ON omnix_rpg_narration_events (created_at, event_id);

INSERT INTO omnix_retention_policies (record_type, retention_days, terminal_only)
VALUES ('rpg_narration_events', 1, FALSE)
ON CONFLICT (record_type) DO NOTHING;
