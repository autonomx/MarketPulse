-- omnix-migration: phase=expand transactional=false
-- WP-5.7: a chat turn loads only the newest part of its transcript, so the
-- idempotent retry of a user turn is found by its user_turn_id through this
-- index instead of in the loaded messages. Built concurrently so chat writes
-- continue. If a build is interrupted, drop the INVALID index and run the
-- migration again.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_omnix_chat_messages_user_turn
    ON omnix_chat_messages (workspace_id, session_id, (metadata->>'user_turn_id'))
    WHERE role = 'user' AND metadata ? 'user_turn_id'
