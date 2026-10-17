-- Session-scoped lookup for distributed admission and generation ordering.
CREATE INDEX IF NOT EXISTS idx_omnix_jobs_chat_session
    ON omnix_jobs (workspace_id, (input_payload ->> 'session_id'), created_at, id)
    WHERE job_type = 'chat.generate';
