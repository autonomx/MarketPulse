-- omnix-migration: phase=expand transactional=true
-- WP-8.5: Memory v1 is retired; curated memory records live in Memory v2 only.
--
-- An installation still under v1 authority moves to v2 here, the way the
-- operator switch did when v1 held no records (an empty readiness aggregate).
-- One whose v1 still holds records is refused: switch it with a release that
-- still has the v1 import (python -m app.platform.assistant_memory.v2.cutover
-- activate) first. The v1 tables stay; candidates and session snapshots live
-- there.
DO $$
DECLARE
    current_authority TEXT;
    current_epoch_id BIGINT;
    next_epoch BIGINT;
BEGIN
    -- Row-level security applies to the owner too: count every workspace's rows.
    PERFORM set_config('omnix.system', 'on', true);
    SELECT e.authority, c.current_epoch
      INTO current_authority, current_epoch_id
      FROM omnix_memory_v2_authority_current c
      JOIN omnix_memory_v2_authority_epochs e ON e.epoch = c.current_epoch
     WHERE c.singleton = TRUE
       FOR UPDATE OF c;
    IF current_authority IS DISTINCT FROM 'v1' THEN
        PERFORM set_config('omnix.system', '', true);
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM omnix_memory_records) THEN
        RAISE EXCEPTION 'Memory v1 still holds curated records; switch to Memory v2 with the previous release before upgrading';
    END IF;
    SELECT COALESCE(MAX(epoch), 0) + 1 INTO next_epoch FROM omnix_memory_v2_authority_epochs;
    INSERT INTO omnix_memory_v2_authority_epochs (
        epoch, authority, activated_at, previous_epoch, readiness,
        readiness_receipt_ids, activated_by, reason
    ) VALUES (
        next_epoch, 'v2', CURRENT_TIMESTAMP, current_epoch_id,
        '{"ready": true, "watermarks": {"observation": 0, "consolidation": 0, "graph_revision": 0, "derived_revision": 0, "authoritative_event": 0, "governance_revision": 0, "index_graph_revision": 0, "index_derived_revision": 0, "derived_observation_watermark": 0}, "indexes_caught_up": true, "shadow_quality_passed": true, "graph_validation_passed": true}'::jsonb,
        '[]'::jsonb, 'migration:memory-v1-retired', 'Memory v1 retired with no curated records'
    );
    UPDATE omnix_memory_v2_authority_current
       SET current_epoch = next_epoch, updated_at = CURRENT_TIMESTAMP
     WHERE singleton = TRUE;
    PERFORM set_config('omnix.system', '', true);
END $$;
