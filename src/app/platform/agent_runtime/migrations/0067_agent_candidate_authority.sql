-- Candidate-bound validation, canonical review subject, and provider usage availability.

ALTER TABLE omnix_agent_validation_results
    ADD COLUMN IF NOT EXISTS outcome TEXT;

UPDATE omnix_agent_validation_results
   SET outcome = CASE
       WHEN success THEN 'passed'
       WHEN metadata ->> 'outcome' IN (
           'substantive_failure','infrastructure_failure','protocol_failure','blocked'
       ) THEN metadata ->> 'outcome'
       ELSE 'substantive_failure'
   END
 WHERE outcome IS NULL;

ALTER TABLE omnix_agent_validation_results
    ALTER COLUMN outcome SET DEFAULT 'substantive_failure',
    ALTER COLUMN outcome SET NOT NULL;

ALTER TABLE omnix_agent_validation_results
    DROP CONSTRAINT IF EXISTS omnix_agent_validation_results_outcome_check;
ALTER TABLE omnix_agent_validation_results
    ADD CONSTRAINT omnix_agent_validation_results_outcome_check
    CHECK (outcome IN ('passed','substantive_failure','infrastructure_failure','protocol_failure','blocked'));

ALTER TABLE omnix_agent_review_snapshots
    ADD COLUMN IF NOT EXISTS run_change_set_id TEXT,
    ADD COLUMN IF NOT EXISTS subject_paths JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS context_paths JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE omnix_agent_run_usage
    ADD COLUMN IF NOT EXISTS input_tokens_reported BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS output_tokens_reported BOOLEAN NOT NULL DEFAULT FALSE;
