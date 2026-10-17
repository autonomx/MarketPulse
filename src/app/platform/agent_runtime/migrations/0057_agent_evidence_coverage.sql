-- Phase 15: persist first-class evidence coverage identity.
ALTER TABLE omnix_agent_evidence_receipts
    ADD COLUMN IF NOT EXISTS coverage JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_omnix_agent_evidence_receipts_coverage
    ON omnix_agent_evidence_receipts
    USING GIN (coverage);
