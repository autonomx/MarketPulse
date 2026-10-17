-- omnix-migration: phase=expand transactional=true
-- WP-5.6: compress the RPG state columns with lz4 instead of pglz. A turn
-- rewrites the campaign's whole state; on a 1.7 MB state lz4 writes in 21 ms
-- against pglz's 31 and stores 118 KB against 193. Only new values use it;
-- existing values are read as they are. A server built without lz4 keeps
-- pglz.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_settings
         WHERE name = 'default_toast_compression' AND 'lz4' = ANY(enumvals)
    ) THEN
        ALTER TABLE omnix_rpg_campaigns ALTER COLUMN state_jsonb SET COMPRESSION lz4;
        ALTER TABLE omnix_rpg_snapshots ALTER COLUMN snapshot_jsonb SET COMPRESSION lz4;
        ALTER TABLE omnix_rpg_turns ALTER COLUMN state_delta_jsonb SET COMPRESSION lz4;
    END IF;
END $$;
