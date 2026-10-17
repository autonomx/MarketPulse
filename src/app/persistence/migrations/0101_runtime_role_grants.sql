-- omnix-migration: phase=expand transactional=true
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omnix_app') THEN
        GRANT USAGE ON SCHEMA public TO omnix_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO omnix_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omnix_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO omnix_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO omnix_app;
    ELSE
        RAISE NOTICE 'omnix_app role does not exist; runtime grants skipped';
    END IF;
END
$$;
