-- Keep the durable source-format contract aligned with audiobook ingestion.
ALTER TABLE omnix_audiobook_source_revisions
    DROP CONSTRAINT IF EXISTS omnix_audiobook_source_revisions_source_format_check;

ALTER TABLE omnix_audiobook_source_revisions
    ADD CONSTRAINT omnix_audiobook_source_revisions_source_format_check
    CHECK (source_format IN (
        'docx', 'epub', 'htm', 'html', 'markdown', 'md', 'pdf', 'text', 'txt'
    ));
