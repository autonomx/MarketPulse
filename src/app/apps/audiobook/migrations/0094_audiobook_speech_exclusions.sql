-- User-selected speech omissions, bound to canonical chapter content.
CREATE TABLE IF NOT EXISTS omnix_audiobook_speech_exclusions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    chapter_hash TEXT NOT NULL,
    chapter_ordinal INTEGER NOT NULL CHECK (chapter_ordinal >= 0),
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
    source_text TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, project_id, chapter_hash, chapter_ordinal, start_offset, end_offset),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES omnix_audiobook_projects(workspace_id, id)
);
