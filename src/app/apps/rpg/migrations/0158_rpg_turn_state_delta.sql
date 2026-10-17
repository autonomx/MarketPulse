-- omnix-migration: phase=expand transactional=true
-- WP-5.6: each turn row carries the JSON Patch (RFC 6902) of its state change
-- (app.apps.rpg.foundation.persistence.state_delta). With a full snapshot
-- only every OMNIX_RPG_SNAPSHOT_EVERY revisions, a state is a snapshot plus
-- the patches of the turns after it, checked by each turn's state_hash_after.
-- Turns committed before this migration have no patch.
ALTER TABLE omnix_rpg_turns ADD COLUMN IF NOT EXISTS state_delta_jsonb JSONB;
