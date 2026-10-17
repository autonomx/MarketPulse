-- Older local installations created this PostgreSQL-truncated unique constraint
-- before scoped/retry generation was introduced. Its generated name differs from
-- the canonical name removed by migrations 0026 and 0030, so explicitly remove
-- it to allow multiple durable attempts for the same world draft revision.
ALTER TABLE omnix_rpg_world_generation_runs
    DROP CONSTRAINT IF EXISTS omnix_rpg_world_generation_ru_workspace_id_world_id_draft_r_key;

