-- Governed browser assertions are durable coding-quality validation evidence.
-- Migration 0061 predated browser validation and omitted its kind from the
-- constraint, causing otherwise successful browser proofs to be discarded.
ALTER TABLE omnix_agent_validation_results
    DROP CONSTRAINT IF EXISTS omnix_agent_validation_results_kind_check;

ALTER TABLE omnix_agent_validation_results
    ADD CONSTRAINT omnix_agent_validation_results_kind_check
    CHECK (kind IN ('test','typecheck','lint','build','diff_review','browser','custom'));
