-- Legacy review rows may contain schema-invalid provider output.  Under the
-- authored-draft boundary they are failure evidence, never canonical candidates.
UPDATE omnix_rpg_world_generation_topic_results
SET
    status = 'failed',
    candidate_jsonb = NULL,
    candidate_hash = '',
    validation_jsonb = jsonb_build_object(
        'schema_version', 'rpg_world_generation_review_v2',
        'status', 'failed',
        'blocking', TRUE,
        'validation_status', 'failed',
        'reason_codes', jsonb_build_array('legacy_candidate_missing_contract_receipt'),
        'issues', jsonb_build_array(
            jsonb_build_object(
                'code', 'legacy_candidate_missing_contract_receipt',
                'topic_id', topic_id,
                'entity_id', '',
                'field_id', 'provenance.authoritative_contract_receipt',
                'message', 'Legacy retained output was migrated to a non-acceptable failure artifact.'
            )
        ),
        'summary', 'Legacy retained output is not a canonical World Forge candidate.'
    ),
    provider_jsonb = provider_jsonb || jsonb_build_object(
        'failure_artifact',
        jsonb_build_object(
            'schema_version', 'rpg_world_forge_failure_artifact_v1',
            'artifact_id', 'legacy:' || left(replace(candidate_hash, 'sha256:', ''), 24),
            'run_id', run_id,
            'job_id', job_id,
            'topic_id', topic_id,
            'attempt', 1,
            'stage', 'contract_mismatch',
            'provider', COALESCE(provider_jsonb->>'provider', ''),
            'model', COALESCE(provider_jsonb->>'model', ''),
            'structured_mode', COALESCE(provider_jsonb->>'response_format', ''),
            'strategy_identity', '',
            'provider_schema_hash', COALESCE(provider_jsonb->>'schema_hash', ''),
            'canonical_contract_hash', '',
            'raw_response_hash', candidate_hash,
            'raw_response_bytes', 0,
            'sanitized_excerpt', '',
            'issues', jsonb_build_array(
                jsonb_build_object(
                    'stage', 'contract_mismatch',
                    'path', '/provenance/authoritative_contract_receipt',
                    'code', 'legacy_candidate_missing_contract_receipt',
                    'message', 'Candidate predates the authoritative authored-draft contract.'
                )
            ),
            'deterministic_repairs', '[]'::jsonb,
            'correction_attempted', FALSE,
            'correction_result', 'not_attempted',
            'created_at', to_jsonb(updated_at),
            'retention_policy', 'world_generation_diagnostic'
        )
    ),
    updated_at = CURRENT_TIMESTAMP
WHERE status = 'needs_review'
  AND candidate_jsonb IS NOT NULL
  AND COALESCE(
      candidate_jsonb #>> '{provenance,authoritative_contract_receipt,schema_version}',
      ''
  ) <> 'rpg_world_forge_contract_receipt_v1';

COMMENT ON TABLE omnix_rpg_world_generation_topic_results IS
    'Per-attempt World Forge outcomes: canonical candidates have authoritative receipts; failed rows retain diagnostics only.';
