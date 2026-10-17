-- omnix-migration: phase=expand transactional=true
-- WP-4.8: the audit trail is append-only.
--
-- Rows can be inserted, never changed or deleted by the application. Two
-- cases still pass: foreign-key actions when a workspace or user is deleted
-- (they run as nested triggers), and retention, which sets
-- omnix.audit_maintenance in its own transaction (WP-5.2).

CREATE OR REPLACE FUNCTION omnix_audit_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF pg_trigger_depth() > 1 OR current_setting('omnix.audit_maintenance', true) = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'omnix_audit_events is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

DROP TRIGGER IF EXISTS omnix_audit_events_append_only ON omnix_audit_events;
CREATE TRIGGER omnix_audit_events_append_only
    BEFORE UPDATE OR DELETE ON omnix_audit_events
    FOR EACH ROW EXECUTE FUNCTION omnix_audit_events_append_only();

-- A separate runtime role also loses the privileges outright.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omnix_app') THEN
        REVOKE UPDATE, DELETE ON omnix_audit_events FROM omnix_app;
    END IF;
END
$$;
