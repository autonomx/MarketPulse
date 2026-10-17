-- omnix-migration: phase=expand transactional=false
-- WP-5.7: history search uses a full-text index instead of scanning every
-- message with ILIKE. Built concurrently so chat writes continue. If a build
-- is interrupted, drop the INVALID index and run the migration again.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_omnix_chat_messages_search
    ON omnix_chat_messages USING gin (to_tsvector('simple', content))
