ALTER TABLE omnix_audiobook_speakers
    ADD COLUMN IF NOT EXISTS analysis_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
