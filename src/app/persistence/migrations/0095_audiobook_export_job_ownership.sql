-- Repair project ownership for export jobs created before it was included in
-- the payload. Project cancellation and deletion use this durable binding.
UPDATE omnix_jobs AS j
   SET input_payload = jsonb_set(j.input_payload, '{project_id}',
                                to_jsonb(m.project_id), true),
       updated_at = CURRENT_TIMESTAMP
  FROM omnix_audiobook_export_manifests AS m
 WHERE j.workspace_id = m.workspace_id AND j.id = m.job_id
   AND j.module = 'audiobook' AND j.job_type = 'audiobook.export'
   AND j.input_payload->>'manifest_id' = m.id
   AND NOT j.input_payload ? 'project_id';
