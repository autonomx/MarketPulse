-- omnix-migration: phase=expand transactional=true
-- WP-5.2: retention policies executed by app.persistence.retention.
--
-- New policies use the roadmap defaults. Trading strategy events are kept
-- disabled: qualification windows read long histories, so enabling their
-- retention is an operator decision. Audit retention runs only through the
-- maintenance command (the runtime role cannot delete audit rows).

INSERT INTO omnix_retention_policies (record_type, retention_days, terminal_only, enabled, metadata)
VALUES
    ('jobs', 30, TRUE, TRUE, '{"cascades": ["job_events", "job_logs", "job_attempts"]}'::jsonb),
    ('agent_run_events', 30, TRUE, TRUE, '{"keeps": "milestone and evidence events"}'::jsonb),
    ('trading_strategy_events', 180, FALSE, FALSE, '{"enable": "after confirming qualification windows"}'::jsonb),
    ('runtime_nodes', 7, TRUE, TRUE, '{}'::jsonb),
    ('auth_sessions', 7, TRUE, TRUE, '{}'::jsonb)
ON CONFLICT (record_type) DO NOTHING;

-- Published outbox rows and processed inbox rows: 7 days (was 30), unless an
-- operator already changed the value.
UPDATE omnix_retention_policies
   SET retention_days = 7, updated_at = CURRENT_TIMESTAMP
 WHERE record_type IN ('outbox_events', 'outbox_consumer_inbox') AND retention_days = 30;

UPDATE omnix_retention_policies
   SET metadata = metadata || '{"maintenance_only": true}'::jsonb, updated_at = CURRENT_TIMESTAMP
 WHERE record_type = 'audit_events';
