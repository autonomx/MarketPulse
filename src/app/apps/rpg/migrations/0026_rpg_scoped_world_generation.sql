ALTER TABLE omnix_rpg_world_generation_runs
    DROP CONSTRAINT IF EXISTS omnix_rpg_world_generation_runs_workspace_id_world_id_draft_revision_key;

CREATE INDEX IF NOT EXISTS omnix_rpg_world_generation_runs_world_draft_idx
    ON omnix_rpg_world_generation_runs (
        workspace_id, world_id, draft_revision, created_at DESC
    );

COMMENT ON TABLE omnix_rpg_world_generation_runs IS
    'Durable full or scoped world-generation runs; multiple immutable run identities may target one draft revision.';
