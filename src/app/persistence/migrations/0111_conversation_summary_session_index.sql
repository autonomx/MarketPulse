-- omnix-migration: phase=expand transactional=false
-- WP-5.7: a session's conversation summaries are found by index instead of
-- scanning up to 5,000 summary documents of the workspace.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_omnix_module_records_chat_summary_session
    ON omnix_module_records (workspace_id, (payload->>'session_id'), updated_at DESC)
    WHERE module = 'chat' AND record_type = 'conversation-summary'
