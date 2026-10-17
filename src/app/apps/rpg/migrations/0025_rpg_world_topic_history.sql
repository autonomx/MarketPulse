CREATE TABLE IF NOT EXISTS omnix_rpg_world_topic_history (
    history_sequence BIGSERIAL PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    draft_revision BIGINT NOT NULL CHECK (draft_revision >= 1),
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    content_jsonb JSONB NOT NULL,
    directives_jsonb JSONB NOT NULL,
    dependency_hashes_jsonb JSONB NOT NULL,
    input_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    provenance_jsonb JSONB NOT NULL,
    topic_updated_at TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (workspace_id, world_id)
        REFERENCES omnix_rpg_worlds (workspace_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_world_topic_history_latest_idx
    ON omnix_rpg_world_topic_history (
        workspace_id, world_id, draft_revision, topic_id, history_sequence DESC
    );

INSERT INTO omnix_rpg_world_topic_history (
    workspace_id, world_id, topic_id, draft_revision, source, status,
    content_jsonb, directives_jsonb, dependency_hashes_jsonb,
    input_hash, content_hash, provenance_jsonb, topic_updated_at
)
SELECT workspace_id, world_id, topic_id, draft_revision, source, status,
       content_jsonb, directives_jsonb, dependency_hashes_jsonb,
       input_hash, content_hash, provenance_jsonb, updated_at
  FROM omnix_rpg_world_topics;

CREATE OR REPLACE FUNCTION omnix_capture_rpg_world_topic_history()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO omnix_rpg_world_topic_history (
        workspace_id, world_id, topic_id, draft_revision, source, status,
        content_jsonb, directives_jsonb, dependency_hashes_jsonb,
        input_hash, content_hash, provenance_jsonb, topic_updated_at
    ) VALUES (
        NEW.workspace_id, NEW.world_id, NEW.topic_id, NEW.draft_revision,
        NEW.source, NEW.status, NEW.content_jsonb, NEW.directives_jsonb,
        NEW.dependency_hashes_jsonb, NEW.input_hash, NEW.content_hash,
        NEW.provenance_jsonb, NEW.updated_at
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS omnix_rpg_world_topic_history_trigger
    ON omnix_rpg_world_topics;

CREATE TRIGGER omnix_rpg_world_topic_history_trigger
AFTER INSERT OR UPDATE ON omnix_rpg_world_topics
FOR EACH ROW
EXECUTE FUNCTION omnix_capture_rpg_world_topic_history();

ALTER TABLE omnix_rpg_world_generation_runs
    ADD COLUMN parent_run_id TEXT,
    ADD COLUMN lineage_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE omnix_rpg_world_generation_runs
    ADD CONSTRAINT omnix_rpg_world_generation_runs_parent_fk
    FOREIGN KEY (workspace_id, parent_run_id)
    REFERENCES omnix_rpg_world_generation_runs (workspace_id, run_id)
    ON DELETE RESTRICT;

CREATE INDEX omnix_rpg_world_generation_runs_lineage_idx
    ON omnix_rpg_world_generation_runs (
        workspace_id, world_id, draft_revision DESC, parent_run_id
    );

COMMENT ON TABLE omnix_rpg_world_topic_history IS
    'Append-only snapshots of reusable-world topic drafts for review and rollback.';

COMMENT ON COLUMN omnix_rpg_world_generation_runs.parent_run_id IS
    'Previous draft generation run in the same reusable-world lineage.';

COMMENT ON COLUMN omnix_rpg_world_generation_runs.lineage_jsonb IS
    'Deterministic parent/root and draft lineage metadata for review and rollback.';
