CREATE TABLE IF NOT EXISTS omnix_audiobook_chapter_assemblies (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    assembly_key TEXT NOT NULL CHECK (length(assembly_key) = 64),
    render_ids JSONB NOT NULL,
    audio_asset_id TEXT NOT NULL UNIQUE REFERENCES omnix_assets(id),
    audio_checksum TEXT NOT NULL CHECK (length(audio_checksum) = 64),
    duration_seconds DOUBLE PRECISION NOT NULL CHECK (duration_seconds > 0),
    sample_rate INTEGER NOT NULL CHECK (sample_rate > 0),
    pause_policy JSONB NOT NULL,
    loudness JSONB NOT NULL,
    timeline JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (workspace_id, chapter_id) REFERENCES omnix_audiobook_chapters(workspace_id, id)
);

CREATE INDEX IF NOT EXISTS idx_audiobook_chapter_assembly_cache
    ON omnix_audiobook_chapter_assemblies (workspace_id, chapter_id, assembly_key, created_at DESC);

CREATE TABLE IF NOT EXISTS omnix_audiobook_export_manifests (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL,
    job_id TEXT NOT NULL UNIQUE REFERENCES omnix_jobs(id),
    format TEXT NOT NULL CHECK (format IN ('m4b', 'flac', 'wav', 'mp3')),
    manifest JSONB NOT NULL,
    manifest_hash TEXT NOT NULL CHECK (length(manifest_hash) = 64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (workspace_id, project_id) REFERENCES omnix_audiobook_projects(workspace_id, id),
    FOREIGN KEY (workspace_id, source_revision_id) REFERENCES omnix_audiobook_source_revisions(workspace_id, id)
);

CREATE TRIGGER audiobook_assembly_immutable BEFORE UPDATE OR DELETE ON omnix_audiobook_chapter_assemblies
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();
CREATE TRIGGER audiobook_manifest_immutable BEFORE UPDATE OR DELETE ON omnix_audiobook_export_manifests
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();
