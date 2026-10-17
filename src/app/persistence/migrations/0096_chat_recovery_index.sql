-- Recovery scans only active inline Chat jobs, in stable keyset order.
CREATE INDEX IF NOT EXISTS idx_omnix_jobs_chat_recovery
    ON omnix_jobs (workspace_id, created_at, id)
    WHERE job_type = 'chat.generate'
      AND status IN ('queued', 'leased', 'running', 'waiting', 'retrying', 'cancel_requested')
      AND metadata #>> '{compat_contract,compat,inline_execution}' = 'true';
