-- Durable document-structure interpretation over immutable audiobook source spans.
CREATE TABLE IF NOT EXISTS omnix_audiobook_structure_runs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL,
    analyzer_version TEXT NOT NULL,
    classifier JSONB NOT NULL DEFAULT '{}'::jsonb,
    ai_fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, source_revision_id)
        REFERENCES omnix_audiobook_source_revisions(workspace_id, id)
);

CREATE INDEX IF NOT EXISTS idx_audiobook_structure_runs_revision
    ON omnix_audiobook_structure_runs(workspace_id, source_revision_id, created_at DESC);

CREATE TABLE IF NOT EXISTS omnix_audiobook_document_blocks (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    structure_run_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
    source_span_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    original_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    content_role TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    provenance JSONB NOT NULL DEFAULT '[]'::jsonb,
    recurrence_group TEXT,
    structure_quality TEXT NOT NULL CHECK (structure_quality IN ('HIGH', 'MEDIUM', 'LOW')),
    page_index INTEGER,
    page_block_index INTEGER,
    reading_order INTEGER,
    distance_from_top DOUBLE PRECISION,
    distance_from_bottom DOUBLE PRECISION,
    bounding_box JSONB,
    font_size DOUBLE PRECISION,
    font_weight TEXT,
    font_style TEXT,
    parent_block_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, id, structure_run_id),
    UNIQUE (structure_run_id, chapter_id, start_offset),
    FOREIGN KEY (workspace_id, structure_run_id)
        REFERENCES omnix_audiobook_structure_runs(workspace_id, id),
    FOREIGN KEY (workspace_id, source_revision_id)
        REFERENCES omnix_audiobook_source_revisions(workspace_id, id),
    FOREIGN KEY (workspace_id, chapter_id)
        REFERENCES omnix_audiobook_chapters(workspace_id, id)
);

CREATE INDEX IF NOT EXISTS idx_audiobook_document_blocks_chapter
    ON omnix_audiobook_document_blocks(workspace_id, chapter_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_audiobook_document_blocks_role
    ON omnix_audiobook_document_blocks(workspace_id, source_revision_id, content_role);
CREATE INDEX IF NOT EXISTS idx_audiobook_document_blocks_recurrence
    ON omnix_audiobook_document_blocks(workspace_id, source_revision_id, recurrence_group)
    WHERE recurrence_group IS NOT NULL;

CREATE TABLE IF NOT EXISTS omnix_audiobook_structural_regions (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    structure_run_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
    block_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    content_role TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    provenance JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, id, structure_run_id),
    FOREIGN KEY (workspace_id, structure_run_id)
        REFERENCES omnix_audiobook_structure_runs(workspace_id, id),
    FOREIGN KEY (workspace_id, source_revision_id)
        REFERENCES omnix_audiobook_source_revisions(workspace_id, id),
    FOREIGN KEY (workspace_id, chapter_id)
        REFERENCES omnix_audiobook_chapters(workspace_id, id)
);

CREATE INDEX IF NOT EXISTS idx_audiobook_structural_regions_chapter
    ON omnix_audiobook_structural_regions(workspace_id, chapter_id, start_offset);

CREATE TABLE IF NOT EXISTS omnix_audiobook_document_overrides (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    scope TEXT NOT NULL CHECK (
        scope IN ('BLOCK', 'REGION', 'RECURRENCE_GROUP', 'DOCUMENT_ROLE')
    ),
    scope_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN ('DEFAULT', 'READ', 'SKIP', 'READ_ONCE')
    ),
    role_override TEXT,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, project_id, source_revision_id, scope, scope_key, revision),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES omnix_audiobook_projects(workspace_id, id),
    FOREIGN KEY (workspace_id, source_revision_id)
        REFERENCES omnix_audiobook_source_revisions(workspace_id, id)
);

CREATE INDEX IF NOT EXISTS idx_audiobook_document_overrides_latest
    ON omnix_audiobook_document_overrides(
        workspace_id, project_id, source_revision_id, scope, scope_key, revision DESC
    );

CREATE TRIGGER audiobook_structure_run_immutable
    BEFORE UPDATE OR DELETE ON omnix_audiobook_structure_runs
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();

CREATE TRIGGER audiobook_document_block_immutable
    BEFORE UPDATE OR DELETE ON omnix_audiobook_document_blocks
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();

CREATE TRIGGER audiobook_structural_region_immutable
    BEFORE UPDATE OR DELETE ON omnix_audiobook_structural_regions
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();

CREATE TRIGGER audiobook_document_override_immutable
    BEFORE UPDATE OR DELETE ON omnix_audiobook_document_overrides
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();
