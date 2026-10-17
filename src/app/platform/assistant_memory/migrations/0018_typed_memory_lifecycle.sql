ALTER TABLE omnix_memory_records
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'semantic_fact',
    ADD COLUMN IF NOT EXISTS structured_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS supersedes_memory_id TEXT,
    ADD COLUMN IF NOT EXISTS contradiction_group TEXT;

ALTER TABLE omnix_memory_candidates
    ADD COLUMN IF NOT EXISTS proposed_kind TEXT NOT NULL DEFAULT 'semantic_fact',
    ADD COLUMN IF NOT EXISTS proposed_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS proposed_supersedes_memory_id TEXT;

ALTER TABLE omnix_memory_records
    DROP CONSTRAINT IF EXISTS chk_omnix_memory_record_kind,
    ADD CONSTRAINT chk_omnix_memory_record_kind CHECK (
        kind IN (
            'semantic_fact', 'preference', 'instruction', 'relationship_state',
            'episode', 'routine', 'goal', 'open_loop', 'temporal_fact',
            'pronunciation'
        )
    );

ALTER TABLE omnix_memory_candidates
    DROP CONSTRAINT IF EXISTS chk_omnix_memory_candidate_kind,
    ADD CONSTRAINT chk_omnix_memory_candidate_kind CHECK (
        proposed_kind IN (
            'semantic_fact', 'preference', 'instruction', 'relationship_state',
            'episode', 'routine', 'goal', 'open_loop', 'temporal_fact',
            'pronunciation'
        )
    );

CREATE INDEX IF NOT EXISTS idx_omnix_memory_kind_status
    ON omnix_memory_records
        (workspace_id, owner_type, owner_id, kind, status, updated_at DESC, id);

CREATE INDEX IF NOT EXISTS idx_omnix_memory_contradiction_group
    ON omnix_memory_records
        (workspace_id, owner_type, owner_id, contradiction_group, status)
    WHERE contradiction_group IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_omnix_memory_candidate_kind_status
    ON omnix_memory_candidates
        (workspace_id, proposed_owner_type, proposed_owner_id,
         proposed_kind, status, created_at, id);
