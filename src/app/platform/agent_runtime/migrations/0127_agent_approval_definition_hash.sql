-- omnix-migration: phase=expand transactional=true
-- PA-1.4 (ADR-0016): an agent approval records the hash of its capability's
-- authority-relevant definition when issued. Execution refuses an approval
-- whose capability is missing or whose definition changed since; approvals
-- issued before this migration have no hash and are refused, so the run asks
-- for approval again.
ALTER TABLE omnix_agent_approvals
    ADD COLUMN IF NOT EXISTS capability_definition_hash TEXT
        CHECK (capability_definition_hash IS NULL OR capability_definition_hash ~ '^[0-9a-f]{64}$');
