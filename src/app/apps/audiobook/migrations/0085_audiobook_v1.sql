-- Audiobook source, interpretation, rendering, and export are separate revisions.
CREATE TABLE IF NOT EXISTS omnix_audiobook_projects (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'en',
    cover_asset_id TEXT REFERENCES omnix_assets(id),
    current_source_revision_id TEXT,
    state TEXT NOT NULL DEFAULT 'imported',
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    settings_revision BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS omnix_audiobook_source_revisions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    original_asset_id TEXT NOT NULL REFERENCES omnix_assets(id),
    original_asset_hash TEXT NOT NULL CHECK (length(original_asset_hash) = 64),
    source_format TEXT NOT NULL CHECK (source_format IN ('epub', 'txt', 'md')),
    extractor_version TEXT NOT NULL,
    extraction_settings JSONB NOT NULL,
    extraction_settings_hash TEXT NOT NULL CHECK (length(extraction_settings_hash) = 64),
    canonical_hash TEXT NOT NULL CHECK (length(canonical_hash) = 64),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, project_id) REFERENCES omnix_audiobook_projects(workspace_id, id)
);

ALTER TABLE omnix_audiobook_projects
    ADD CONSTRAINT omnix_audiobook_current_source_fk
    FOREIGN KEY (workspace_id, current_source_revision_id)
    REFERENCES omnix_audiobook_source_revisions(workspace_id, id);

CREATE TABLE IF NOT EXISTS omnix_audiobook_chapters (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    title TEXT NOT NULL,
    canonical_text TEXT NOT NULL,
    canonical_hash TEXT NOT NULL CHECK (length(canonical_hash) = 64),
    structure JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, id),
    UNIQUE (source_revision_id, ordinal),
    FOREIGN KEY (workspace_id, source_revision_id) REFERENCES omnix_audiobook_source_revisions(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS omnix_audiobook_spans (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
    source_text TEXT NOT NULL,
    source_hash TEXT NOT NULL CHECK (length(source_hash) = 64),
    structural_kind TEXT NOT NULL DEFAULT 'narration',
    detector_version TEXT NOT NULL,
    UNIQUE (workspace_id, id),
    UNIQUE (chapter_id, ordinal),
    UNIQUE (chapter_id, start_offset),
    FOREIGN KEY (workspace_id, chapter_id) REFERENCES omnix_audiobook_chapters(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS omnix_audiobook_speakers (
    id UUID PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'character',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, project_id) REFERENCES omnix_audiobook_projects(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS omnix_audiobook_speaker_aliases (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    speaker_id UUID NOT NULL,
    alias TEXT NOT NULL,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    confirmed_by_user_id TEXT REFERENCES omnix_users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (workspace_id, project_id) REFERENCES omnix_audiobook_projects(workspace_id, id),
    FOREIGN KEY (workspace_id, speaker_id) REFERENCES omnix_audiobook_speakers(workspace_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_audiobook_confirmed_alias
    ON omnix_audiobook_speaker_aliases(project_id, lower(alias)) WHERE status = 'confirmed';

CREATE TABLE IF NOT EXISTS omnix_audiobook_castings (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    speaker_id UUID NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    voice_profile_id TEXT NOT NULL,
    voice_revision_hash TEXT NOT NULL CHECK (length(voice_revision_hash) = 64),
    style JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, id),
    UNIQUE (speaker_id, revision),
    FOREIGN KEY (workspace_id, speaker_id) REFERENCES omnix_audiobook_speakers(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS omnix_audiobook_annotations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    role TEXT NOT NULL,
    speaker_id UUID,
    speaker_candidate TEXT,
    delivery TEXT NOT NULL DEFAULT '',
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    classifier JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, id),
    UNIQUE (span_id, revision),
    FOREIGN KEY (workspace_id, span_id) REFERENCES omnix_audiobook_spans(workspace_id, id),
    FOREIGN KEY (workspace_id, speaker_id) REFERENCES omnix_audiobook_speakers(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS omnix_audiobook_review_issues (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    annotation_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'open',
    resolution JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ,
    FOREIGN KEY (workspace_id, annotation_id) REFERENCES omnix_audiobook_annotations(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS omnix_audiobook_pronunciations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    source_term TEXT NOT NULL,
    spoken_term TEXT NOT NULL,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, source_term, revision),
    FOREIGN KEY (workspace_id, project_id) REFERENCES omnix_audiobook_projects(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS omnix_audiobook_renders (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    render_key TEXT NOT NULL CHECK (length(render_key) = 64),
    annotation_id TEXT NOT NULL,
    casting_id TEXT REFERENCES omnix_audiobook_castings(id),
    speech_plan_hash TEXT NOT NULL CHECK (length(speech_plan_hash) = 64),
    tts_input_text TEXT NOT NULL,
    transformations JSONB NOT NULL DEFAULT '[]'::jsonb,
    provider JSONB NOT NULL,
    generation_settings JSONB NOT NULL,
    audio_asset_id TEXT NOT NULL UNIQUE REFERENCES omnix_assets(id),
    audio_checksum TEXT NOT NULL CHECK (length(audio_checksum) = 64),
    duration_seconds DOUBLE PRECISION NOT NULL CHECK (duration_seconds >= 0),
    sample_rate INTEGER NOT NULL CHECK (sample_rate > 0),
    diagnostics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, render_key),
    FOREIGN KEY (workspace_id, span_id) REFERENCES omnix_audiobook_spans(workspace_id, id),
    FOREIGN KEY (workspace_id, annotation_id) REFERENCES omnix_audiobook_annotations(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS omnix_audiobook_render_batches (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES omnix_jobs(id),
    shard_ordinal INTEGER NOT NULL CHECK (shard_ordinal >= 0),
    start_ordinal INTEGER NOT NULL CHECK (start_ordinal >= 0),
    end_ordinal INTEGER NOT NULL CHECK (end_ordinal > start_ordinal),
    desired_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
    completed_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued',
    retry_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (chapter_id, job_id, shard_ordinal),
    FOREIGN KEY (workspace_id, chapter_id) REFERENCES omnix_audiobook_chapters(workspace_id, id)
);

CREATE TABLE IF NOT EXISTS omnix_audiobook_exports (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL,
    job_id TEXT REFERENCES omnix_jobs(id),
    format TEXT NOT NULL CHECK (format IN ('m4b', 'flac', 'wav', 'mp3')),
    manifest JSONB NOT NULL,
    manifest_hash TEXT NOT NULL CHECK (length(manifest_hash) = 64),
    output_asset_id TEXT NOT NULL UNIQUE REFERENCES omnix_assets(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (workspace_id, project_id) REFERENCES omnix_audiobook_projects(workspace_id, id),
    FOREIGN KEY (workspace_id, source_revision_id) REFERENCES omnix_audiobook_source_revisions(workspace_id, id)
);

-- Source and generated evidence are append-only. Administrative retention must
-- explicitly remove dependent projects through its own controlled path.
CREATE OR REPLACE FUNCTION omnix_audiobook_reject_immutable_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audiobook immutable record cannot be changed: %', TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER audiobook_source_immutable BEFORE UPDATE OR DELETE ON omnix_audiobook_source_revisions
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();
CREATE TRIGGER audiobook_chapter_immutable BEFORE UPDATE OR DELETE ON omnix_audiobook_chapters
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();
CREATE TRIGGER audiobook_span_immutable BEFORE UPDATE OR DELETE ON omnix_audiobook_spans
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();
CREATE TRIGGER audiobook_render_immutable BEFORE UPDATE OR DELETE ON omnix_audiobook_renders
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();
CREATE TRIGGER audiobook_export_immutable BEFORE UPDATE OR DELETE ON omnix_audiobook_exports
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();
