-- omnix-migration: phase=expand transactional=true
-- WP-8.5: once Memory v2 is authoritative, curated memory (saved, approved,
-- edited, pinned, moved, archived) is written as `curated_memory`
-- observations, one per record revision. The event type widens the existing
-- check, so older code keeps working. The index finds a record's current
-- revision by id, for imported v1 records and curated ones alike. Snapshot
-- items stop requiring a v1 record row (see the end).
DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    SELECT c.conname INTO constraint_name
      FROM pg_constraint c
     WHERE c.conrelid = 'omnix_memory_v2_observations'::regclass
       AND c.contype = 'c'
       AND pg_get_constraintdef(c.oid) LIKE '%event_type%';
    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE omnix_memory_v2_observations DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;

ALTER TABLE omnix_memory_v2_observations
    ADD CONSTRAINT omnix_memory_v2_observations_event_type_check CHECK (event_type IN (
        'user_said', 'assistant_generated', 'assistant_delivered',
        'assistant_experienced', 'external_observed', 'system_event',
        'imported_legacy_memory', 'acoustic_observation', 'curated_memory'
    ));

CREATE INDEX IF NOT EXISTS idx_omnix_memory_v2_observations_record
    ON omnix_memory_v2_observations (
        principal_id,
        (COALESCE(payload->'memory_record'->>'id', payload->'legacy_record'->>'id')),
        authority_sequence DESC
    )
    WHERE event_type IN ('curated_memory', 'imported_legacy_memory');

-- Session snapshot items name curated records that, under Memory v2, live in
-- the observation log rather than omnix_memory_records. Every path that
-- deletes a record deletes its snapshot items explicitly.
ALTER TABLE omnix_memory_snapshot_items
    DROP CONSTRAINT IF EXISTS omnix_memory_snapshot_items_memory_record_id_fkey;
