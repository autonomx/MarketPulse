-- Multiple attempts can target the same immutable draft revision.  Runs retain
-- their parent/child lineage so a failed scope can be retried without replacing
-- its history.
ALTER TABLE omnix_rpg_world_generation_runs
    DROP CONSTRAINT IF EXISTS omnix_rpg_world_generation_runs_workspace_id_world_id_draft_revision_key;
